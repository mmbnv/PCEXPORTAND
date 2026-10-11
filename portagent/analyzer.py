"""ANALYZE stage: reconnaissance of an installer or installed game folder.

Produces a :class:`GameProfile` (JSON-serialisable) describing the engine,
runtime APIs, architecture, DRM/anti-cheat and any hard blockers that make a
port impossible.  Detection combines on-disk file signatures with real PE/ELF
import analysis from :mod:`portagent.parsers`.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
from pathlib import Path

from .models import ApiKind, BinaryInfo, Engine, GameProfile, Impossibility
from .parsers import ParsedBinary, parse_file

# ---------------------------------------------------------------------------
# Engine signatures.  Each entry is (glob-or-substring, engine, weight, note).
# Matching is done against the relative lowercase path of every file.
# ---------------------------------------------------------------------------
ENGINE_SIGNATURES: list[tuple[str, Engine, float, str]] = [
    ("unityplayer.dll", Engine.UNITY, 1.0, "UnityPlayer.dll present"),
    ("/il2cpp_data/", Engine.UNITY, 1.0, "IL2CPP runtime data present"),
    ("/mono/", Engine.UNITY, 0.4, "Mono runtime folder"),
    ("/managed/assembly-csharp", Engine.UNITY, 0.9, "Assembly-CSharp managed assembly"),
    ("globalgamemanagers", Engine.UNITY, 1.0, "Unity data archive marker"),
    ("/ue4/", Engine.UNREAL, 0.7, "Unreal Engine 4 content folder"),
    ("/ue5/", Engine.UNREAL, 0.7, "Unreal Engine 5 content folder"),
    ("engine/", Engine.UNREAL, 0.3, "Unreal 'Engine' folder"),
    ("-win64-shipping.exe", Engine.UNREAL, 1.0, "Unreal Shipping executable"),
    ("/binaries/win64/", Engine.UNREAL, 0.8, "Unreal Win64 binaries folder"),
    ("/godot/", Engine.GODOT, 0.7, "Godot project folder"),
    ("godot.windows.", Engine.GODOT, 1.0, "Godot windows template executable"),
    ("godotsharp.dll", Engine.GODOT, 1.0, "Godot .NET (GodotSharp) assemblies"),
    (".pck", Engine.GODOT, 0.6, "Godot PCK resource pack"),
    ("/www/js/plugins/", Engine.RPGMAKER, 0.6, "RPG Maker MV/MZ web assets"),
    ("game.rgssad", Engine.RPGMAKER, 1.0, "RPG Maker XP/VX encrypted archive"),
    ("data/system.rvdata", Engine.RPGMAKER, 1.0, "RPG Maker VX data"),
    ("/data/system.json", Engine.RPGMAKER, 0.8, "RPG Maker MV/MZ data"),
    ("game.unx", Engine.GAMEMAKER, 0.6, "GameMaker data file"),
    ("/data.win", Engine.GAMEMAKER, 1.0, "GameMaker Studio data.win"),
    ("/assets/game.ini", Engine.GAMEMAKER, 0.3, "GameMaker config"),
    ("hl2.exe", Engine.SOURCE, 1.0, "Source engine executable"),
    ("/platform/", Engine.SOURCE, 0.5, "Source platform folder"),
    ("valve.ini", Engine.SOURCE, 0.6, "Source engine config"),
    ("python3", Engine.PYTHON, 0.3, "bundled CPython interpreter"),
    ("/renpy/", Engine.PYTHON, 0.8, "Ren'Py python engine"),
]

# Windows runtime DLLs -> the API a port must satisfy.
DLL_API_MAP: dict[str, ApiKind] = {
    "d3d9.dll": ApiKind.D3D9,
    "d3dx9": ApiKind.D3D9,
    "d3d11.dll": ApiKind.D3D11,
    "d3d12.dll": ApiKind.D3D12,
    "dxgi.dll": ApiKind.D3D11,
    "opengl32.dll": ApiKind.OPENGL,
    "vulkan-1.dll": ApiKind.VULKAN,
    "xaudio2_7.dll": ApiKind.XAUDIO,
    "xaudio2_8.dll": ApiKind.XAUDIO,
    "xaudio2_9.dll": ApiKind.XAUDIO,
    "xapofx": ApiKind.XAUDIO,
    "dsound.dll": ApiKind.DIRECTSOUND,
    "winmm.dll": ApiKind.WINDOWS_MME,
    "sdl2.dll": ApiKind.SDL2,
    "mscoree.dll": ApiKind.DOTNET_IL,
    "mono-2.0-sgen.dll": ApiKind.MONO,
    "jvm.dll": ApiKind.JVM,
}

# DLLs that imply Win32 kernel-level coupling (registry, threads, services).
WIN32_DLLS = {
    "kernel32.dll",
    "user32.dll",
    "gdi32.dll",
    "advapi32.dll",
    "ntdll.dll",
    "shell32.dll",
    "ole32.dll",
    "comdlg32.dll",
    "wininet.dll",
    "ws2_32.dll",
    "iphlpapi.dll",
}

DRM_SIGNATURES: list[tuple[str, str]] = [
    ("steam_api", "Steam DRM / Steamworks wrapper (network-bound, userspace)"),
    ("steamclient", "Steam client API"),
    ("denuvo", "Denuvo (kernel-level, tamper-resistant)"),
    ("drm", "Generic DRM module"),
    ("securom", "SecuROM (kernel driver)"),
    ("safedisc", "SafeDisc (kernel driver)"),
    ("starforce", "StarForce (kernel driver)"),
    ("punkbuster", "PunkBuster anti-cheat (kernel-level)"),
    ("battleye", "BattlEye anti-cheat (kernel-level)"),
    ("easyanticheat", "Easy Anti-Cheat (kernel-level)"),
    ("eac_", "Easy Anti-Cheat component"),
    ("vmprotect", "VMProtect (virtualised, tamper-resistant)"),
    ("themida", "Themida (virtualised, tamper-resistant)"),
]

# Hard blockers: kernel drivers / anti-cheat that cannot run under Android.
KERNEL_ANTICHEAT = {
    "denuvo": "Denuvo requires kernel-level integrity checks and online attestation",
    "securom": "SecuROM installs a kernel-mode driver with a blacklist",
    "safedisc": "SafeDisc relies on Windows CD/DVD driver stack",
    "starforce": "StarForce installs a kernel driver",
    "punkbuster": "PunkBuster kernel component cannot run on Android",
    "battleye": "BattlEye kernel component cannot run on Android",
    "easyanticheat": "Easy Anti-Cheat kernel component cannot run on Android",
    "vmprotect": "VMProtect virtualises code beyond feasible reconstruction",
    "themida": "Themida virtualises code beyond feasible reconstruction",
}

INTERESTING_EXT = {".dll", ".exe", ".so", ".dylib", ".ocx"}


def _sha256(path: Path, limit: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        remaining = limit
        while remaining > 0:
            chunk = f.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _has_clr_header(data: bytes) -> bool:
    """True if a PE image carries a .NET CLI header (managed assembly).

    Reads the PE header, walks the data directories to the
    IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR, and checks for the CLR metadata
    signature ``BSJB``. Pure Python, header-only (no full parse needed).
    """
    if len(data) < 0x40 or data[:2] != b"MZ":
        return False
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        return False
    coff = e_lfanew + 4
    num_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    if opt + opt_size > len(data):
        return False
    magic = struct.unpack_from("<H", data, opt)[0]
    dd_off = opt + (0x70 if magic == 0x20B else 0x60)
    com_off = dd_off + 14 * 8  # IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR
    if com_off + 8 > len(data):
        return False
    rva, size = struct.unpack_from("<II", data, com_off)
    if rva == 0 or size == 0:
        return False
    # Map the RVA to a file offset via the section table, then locate the
    # CLI header and its metadata RVA + the BSDJ signature at that RVA.
    sec = opt + opt_size
    for i in range(num_sections):
        s = sec + i * 40
        if s + 40 > len(data):
            return False
        vsize, vaddr, _rsize, raw = struct.unpack_from("<IIII", data, s + 8)
        if vaddr <= rva < vaddr + max(vsize, 1):
            cli = raw + (rva - vaddr)
            break
    else:
        return False
    cli_size = struct.unpack_from("<I", data, cli + 4)[0] if cli + 8 <= len(data) else 0
    if cli + 32 > len(data) or cli_size == 0:
        return False
    meta_rva = struct.unpack_from("<I", data, cli + 8)[0]
    for i in range(num_sections):
        s = sec + i * 40
        vsize, vaddr, _rsize, raw = struct.unpack_from("<IIII", data, s + 8)
        if vaddr <= meta_rva < vaddr + max(vsize, 1):
            meta = raw + (meta_rva - vaddr)
            return meta + 4 <= len(data) and data[meta:meta + 4] == b"BSJB"
    return False


class Analyzer:
    """Reconnaissance component (mission section 4, ``Analyzer``)."""

    def __init__(self, root: str | Path, deep: bool = True, max_scan_bytes: int = 64 * 1024 * 1024):
        self.root = Path(root)
        self.deep = deep
        self.max_scan_bytes = max_scan_bytes

    def analyze(self) -> GameProfile:
        root = self.root
        if root.is_file():
            # Installer: profile the file itself, note that extraction is needed.
            return self._analyze_installer(root)
        if not root.is_dir():
            raise FileNotFoundError(f"no such file or directory: {root}")

        profile = GameProfile(root=str(root))
        rel_paths: list[str] = []
        exec_paths: list[Path] = []
        total = 0
        assets: dict[str, int] = {}

        for dirpath, _dirnames, filenames in os.walk(root):
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    size = fp.stat().st_size
                except OSError:
                    continue
                total += size
                profile.file_count += 1
                rel = str(fp.relative_to(root)).lower()
                rel_paths.append("/" + rel)
                ext = fp.suffix.lower()
                assets[ext or "(none)"] = assets.get(ext, 0) + 1
                if ext in INTERESTING_EXT or os.access(fp, os.X_OK):
                    exec_paths.append(fp)

        profile.total_size = total
        profile.assets = dict(sorted(assets.items(), key=lambda kv: -kv[1])[:25])
        blob = "\n".join(rel_paths)

        self._detect_engine(profile, rel_paths, blob)
        self._detect_drm(profile, blob)

        # Parse executables for architecture, imports and runtime APIs.
        binaries: list[BinaryInfo] = []
        seen_api: set[ApiKind] = set()
        arch_votes: dict[str, int] = {}
        for fp in exec_paths:
            try:
                if fp.stat().st_size > self.max_scan_bytes:
                    continue
                parsed = parse_file(fp)
            except Exception:
                # Not a parseable PE/ELF (script, resource, packed stub) - skip.
                continue
            info = self._to_binary_info(fp, root, parsed)
            binaries.append(info)
            if parsed.arch != "unknown":
                arch_votes[parsed.arch] = arch_votes.get(parsed.arch, 0) + 1
            for dll in parsed.imports:
                api = self._dll_to_api(dll)
                if api:
                    seen_api.add(api)
                if dll in WIN32_DLLS:
                    seen_api.add(ApiKind.WIN32)
            # IL2CPP / Mono hints from symbol names.
            syms = " ".join(parsed.imported_symbols + parsed.exports).lower()
            if "il2cpp" in syms:
                profile.il2cpp = True
            if "mono_" in syms or "mono_image" in syms:
                profile.managed = True

        profile.binaries = sorted(binaries, key=lambda b: -b.size)[:50]
        profile.arch = max(arch_votes, key=arch_votes.get) if arch_votes else "unknown"
        profile.bitness = 64 if profile.arch.endswith("64") else (32 if arch_votes else None)

        self._detect_il2cpp_on_disk(profile, blob)
        # Managed/CoreCLR titles (Unity Mono, Godot .NET, MonoGame, FNA) often
        # ship managed assemblies whose PE stubs import nothing, so import
        # scanning alone misses them. Probe the assembly contents directly.
        if not profile.il2cpp and not profile.managed:
            self._detect_managed_on_disk(profile, root, rel_paths, blob)
        profile.runtime_apis = sorted(seen_api, key=lambda a: a.value)
        profile.graphics_api = self._pick_graphics(profile.runtime_apis)
        profile.audio_api = self._pick_audio(profile.runtime_apis)
        profile.dependencies = sorted(
            {d for b in profile.binaries for d in b.imports}
        )[:200]
        self._assess_blockers(profile)
        if profile.engine == Engine.UNKNOWN and profile.managed:
            profile.engine = Engine.DOTNET
            profile.engine_evidence.append("managed assemblies with no known engine")
        elif profile.engine == Engine.UNKNOWN and profile.binaries:
            profile.engine = Engine.CUSTOM_NATIVE
            profile.engine_evidence.append("native binaries with no known engine markers")
        return profile

    # -- helpers ----------------------------------------------------------
    def _analyze_installer(self, path: Path) -> GameProfile:
        profile = GameProfile(root=str(path), file_count=1)
        profile.total_size = path.stat().st_size
        profile.notes.append("input is a single file (installer/archive); extraction required")
        try:
            parsed = parse_file(path)
            profile.binaries.append(self._to_binary_info(path, path.parent, parsed))
            profile.arch = parsed.arch
            profile.bitness = parsed.bitness
            blob = " ".join(parsed.imports).lower()
            for dll in parsed.imports:
                api = self._dll_to_api(dll)
                if api:
                    profile.runtime_apis.append(api)
            if re.search(r"inno|nsis|installshield|7z|winrar|sfx", blob):
                profile.notes.append("installer detected: use 7z/innoextract to unpack before ANALYSIS")
        except (ValueError, OSError):
            profile.notes.append("input file is not a PE/ELF binary; treat as archive")
        profile.runtime_apis = sorted(set(profile.runtime_apis), key=lambda a: a.value)
        profile.graphics_api = self._pick_graphics(profile.runtime_apis)
        profile.audio_api = self._pick_audio(profile.runtime_apis)
        self._assess_blockers(profile)
        return profile

    def _to_binary_info(self, fp: Path, root: Path, parsed: ParsedBinary) -> BinaryInfo:
        try:
            rel = str(fp.relative_to(root))
        except ValueError:
            rel = str(fp)
        return BinaryInfo(
            path=rel,
            kind=parsed.kind,
            arch=parsed.arch,
            machine=parsed.machine,
            bitness=parsed.bitness,
            imports=parsed.imports,
            exports=parsed.exports[:64],
            sections=parsed.sections[:32],
            size=fp.stat().st_size,
            sha256=_sha256(fp),
        )

    def _detect_engine(self, profile: GameProfile, rel_paths: list[str], blob: str) -> None:
        scores: dict[Engine, float] = {}
        evidence: dict[Engine, list[str]] = {}
        for pat, engine, weight, note in ENGINE_SIGNATURES:
            if pat in blob:
                scores[engine] = scores.get(engine, 0.0) + weight
                evidence.setdefault(engine, []).append(note)
        # Managed assemblies (.dll with IL) or .NET exe hint.
        if any(p.endswith(".dll") and "assembly-" in p for p in rel_paths):
            scores[Engine.UNITY] = scores.get(Engine.UNITY, 0.0) + 0.5
        if not scores:
            profile.engine = Engine.UNKNOWN
            return
        best = max(scores, key=scores.get)
        profile.engine = best
        profile.engine_confidence = min(1.0, round(scores[best] / 2.0, 3))
        profile.engine_evidence = evidence.get(best, [])

    def _detect_il2cpp_on_disk(self, profile: GameProfile, blob: str) -> None:
        if "il2cpp_data" in blob or "global-metadata.dat" in blob or "il2cpp" in blob:
            profile.il2cpp = True
            if profile.engine == Engine.UNITY:
                profile.engine_evidence.append("IL2CPP metadata found -> native Unity build")
        if not profile.il2cpp and "/mono/" in blob:
            profile.managed = True
            profile.engine_evidence.append("Mono runtime folder found -> managed Unity build")

    def _detect_managed_on_disk(
        self, profile: GameProfile, root: Path, rel_paths: list[str], blob: str
    ) -> None:
        """Confirm a managed/CoreCLR title by inspecting assembly contents."""
        # 1) unmistakable CoreCLR / Mono host filenames.
        coreclr_markers = (
            "/coreclr.dll", "/hostpolicy.dll", "/hostfxr.dll", "/clrjit.dll",
            "/libcoreclr.so", "/libmonosgen", "/mono-2.0-sgen.dll",
        )
        if any(m in blob for m in coreclr_markers):
            profile.managed = True
            profile.engine_evidence.append(
                "CoreCLR/Mono runtime host present -> managed .NET title"
            )
            return
        # 2) inspect a handful of managed-looking assemblies for the CLR header.
        candidates = [
            r for r in rel_paths
            if r.endswith((".dll", ".exe"))
            and ("assembly-" in r or "godotsharp" in r or "/managed/" in r
                 or "monogame" in r or "fna" in r or "sts2" in r)
        ][:12]
        for rel in candidates:
            fp = root / rel.lstrip("/")
            try:
                if fp.stat().st_size > 64 * 1024 * 1024:
                    continue
                head = fp.read_bytes()[:0x4000]
            except OSError:
                continue
            if _has_clr_header(head):
                profile.managed = True
                profile.engine_evidence.append(
                    f"managed assembly ({rel.split('/')[-1]}) carries a CLR header"
                )
                return

    def _detect_drm(self, profile: GameProfile, blob: str) -> None:
        for sig, note in DRM_SIGNATURES:
            if sig in blob:
                profile.drm.append(note)
                if sig in KERNEL_ANTICHEAT:
                    profile.anticheat.append(note)
                    profile.blockers.append(
                        Impossibility(
                            reason=KERNEL_ANTICHEAT[sig],
                            evidence=f"matched kernel anti-cheat/DRM signature '{sig}'",
                            severity="hard",
                        )
                    )

    def _dll_to_api(self, dll: str) -> ApiKind | None:
        for key, api in DLL_API_MAP.items():
            if key in dll:
                return api
        return None

    def _pick_graphics(self, apis: list[ApiKind]) -> ApiKind | None:
        order = [ApiKind.D3D12, ApiKind.D3D11, ApiKind.D3D9, ApiKind.VULKAN, ApiKind.OPENGL]
        for a in order:
            if a in apis:
                return a
        return None

    def _pick_audio(self, apis: list[ApiKind]) -> ApiKind | None:
        order = [ApiKind.XAUDIO, ApiKind.DIRECTSOUND, ApiKind.WINDOWS_MME, ApiKind.SDL2]
        for a in order:
            if a in apis:
                return a
        return None

    def _assess_blockers(self, profile: GameProfile) -> None:
        joined = " ".join(profile.drm + profile.anticheat + profile.dependencies).lower()
        for key, reason in KERNEL_ANTICHEAT.items():
            if key in joined:
                profile.blockers.append(
                    Impossibility(
                        reason=reason,
                        evidence=f"matched kernel anti-cheat/DRM signature '{key}'",
                        severity="hard",
                    )
                )
        # A Windows-only kernel driver (.sys) shipped with the game is a blocker.
        if any(b.path.lower().endswith(".sys") for b in profile.binaries):
            profile.blockers.append(
                Impossibility(
                    reason="ships a Windows kernel driver (.sys) with no Android equivalent",
                    evidence="kernel-mode .sys module present",
                    severity="hard",
                )
            )
        # Unity IL2CPP is feasible; nothing to add.  Mark unknown native arch as soft.
        if profile.arch in ("x86", "x86_64") and not profile.managed and not profile.il2cpp:
            if profile.engine in (Engine.UNKNOWN, Engine.CUSTOM_NATIVE):
                profile.notes.append(
                    "x86/x86_64 native binary without engine metadata: likely needs DBT (Box64) or Wine"
                )
        if profile.arch == "unknown" and profile.engine == Engine.UNKNOWN:
            profile.notes.append("architecture and engine undetermined; analysis incomplete")
