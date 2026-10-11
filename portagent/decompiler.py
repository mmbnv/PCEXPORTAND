"""Decompiler (mission section 4, ``Decompiler``).

Recovers higher-level code from the game's binaries using whichever real tools
are installed, and records a structured inventory of what was recovered:

  * ``jadx``        - APK/DEX -> Java          (Android payloads)
  * ``monodis``     - .NET IL -> IL listing    (Mono / managed assemblies)
  * ``objdump``     - native x86/x64 -> asm    (Win32 executables)
  * ``readelf``     - ELF sections/symbols
  * ``Il2CppDumper``- Unity IL2CPP -> C# stubs (needs global-metadata.dat)

Every tool is optional: the module runs whatever is present and reports exactly
which outputs were produced, never inventing results.  The inventory it returns
is what the LEARN stage stores and what the Translator/Builder consume.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .models import Engine, GameProfile
from .parsers import parse_file

# Extensions we treat as decompilable payloads.
_MANAGED_HINT = (".dll", ".exe")
_NATIVE_HINT = (".exe", ".dll", ".so")


def _run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 600) -> tuple[int, str]:
    env = dict(os.environ)
    env.setdefault("JAVA_HOME", _java_home() or "")
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd[:2])}"


def _java_home() -> str | None:
    jc = shutil.which("javac")
    if not jc:
        return None
    return str(Path(jc).resolve().parent.parent)


@dataclass
class DecompileResult:
    status: str = "partial"  # ok | partial | unavailable
    tools_used: list[str] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)
    recovered: dict[str, int] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "tools_used": self.tools_used,
            "outputs": self.outputs,
            "recovered": self.recovered,
            "commands": self.commands,
            "logs": self.logs[-30:],
        }


def find_tools() -> dict[str, str]:
    """Locate every decompiler that is actually available."""
    tools: dict[str, str] = {}
    jadx = shutil.which("jadx")
    if not jadx:
        cand = Path.home() / ".portagent-tools" / "jadx" / "bin" / "jadx"
        if cand.is_file():
            jadx = str(cand)
    if jadx:
        tools["jadx"] = jadx
    for name in ("monodis", "objdump", "readelf"):
        p = shutil.which(name)
        if p:
            tools[name] = p
    il2cpp = Path.home() / ".portagent-tools" / "il2cppdumper" / "Il2CppDumper.dll"
    if il2cpp.is_file() and shutil.which("dotnet"):
        tools["il2cppdumper"] = str(il2cpp)
    return tools


class Decompiler:
    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def decompile(self, profile: GameProfile) -> DecompileResult:
        res = DecompileResult()
        tools = find_tools()
        if not tools:
            res.status = "unavailable"
            res.logs.append("no decompiler tools found (install jadx / mono / binutils)")
            return res

        # 1) .NET / Mono managed assemblies -> IL via monodis.
        if "monodis" in tools:
            self._decompile_managed(profile, tools, res)

        # 2) Unity IL2CPP -> C# stubs via Il2CppDumper.
        if profile.il2cpp and "il2cppdumper" in tools:
            self._decompile_il2cpp(profile, tools, res)

        # 3) native executables -> disassembly via objdump.
        if "objdump" in tools:
            self._disassemble_native(profile, tools, res)

        if res.outputs:
            res.status = "ok" if "monodis" in res.tools_used or "jadx" in res.tools_used else "partial"
        else:
            res.status = "partial"
        return res

    # -- managed (.NET IL) ------------------------------------------------
    def _decompile_managed(self, profile: GameProfile, tools: dict[str, str],
                           res: DecompileResult) -> None:
        target = self._largest_assembly(profile)
        if target is None:
            return
        out = self.out_dir / "managed"
        out.mkdir(parents=True, exist_ok=True)
        il = out / (Path(target).stem + ".il")
        rc, txt = _run([tools["monodis"], f"--output={il}", str(target)])
        res.commands.append(f"monodis {target}")
        if rc == 0 and il.is_file():
            if "monodis" not in res.tools_used:
                res.tools_used.append("monodis")
            res.outputs["managed_il"] = str(il)
            res.recovered["il_lines"] = il.read_text(encoding="utf-8", errors="ignore").count("\n")
            # Type/method counts for the inventory.
            rc2, types = _run([tools["monodis"], "--typedef", str(target)])
            if rc2 == 0:
                res.recovered["types"] = max(0, types.count("\n") - 1)
            rc3, meths = _run([tools["monodis"], "--method", str(target)])
            if rc3 == 0:
                res.recovered["methods"] = max(0, meths.count("\n") - 1)
        else:
            res.logs.append(f"monodis failed on {target}: {txt[-300:]}")

    def _largest_assembly(self, profile: GameProfile) -> str | None:
        def is_managed(b) -> bool:
            imports = [i.lower() for i in b.imports]
            # Real .NET assemblies import the CLR shim; some obfuscated ones
            # have no imports at all.
            return "mscoree.dll" in imports or (not imports and b.size > 1024)

        cands = [b for b in profile.binaries
                 if b.path.lower().endswith(_MANAGED_HINT) and is_managed(b)]
        if not cands:
            return None
        root = Path(profile.root)
        best = max(cands, key=lambda b: b.size)
        p = root / best.path
        return str(p) if p.is_file() else None

    # -- Unity IL2CPP -----------------------------------------------------
    def _decompile_il2cpp(self, profile: GameProfile, tools: dict[str, str],
                          res: DecompileResult) -> None:
        root = Path(profile.root)
        metadata = None
        for cand in root.rglob("global-metadata.dat"):
            metadata = cand
            break
        if metadata is None:
            res.logs.append("IL2CPP: global-metadata.dat not found")
            return
        binary = self._il2cpp_binary(profile)
        if binary is None:
            res.logs.append("IL2CPP: GameAssembly binary not found")
            return
        out = self.out_dir / "il2cpp"
        out.mkdir(parents=True, exist_ok=True)
        rc, txt = _run(["dotnet", tools["il2cppdumper"], str(binary), str(metadata)],
                       cwd=out, timeout=900)
        res.commands.append(f"il2cppdumper {binary} {metadata}")
        if rc == 0:
            res.tools_used.append("il2cppdumper")
            res.outputs["il2cpp_dir"] = str(out)
            dumps = list(out.rglob("dump.cs"))
            if dumps:
                res.recovered["il2cpp_classes"] = dumps[0].read_text(
                    encoding="utf-8", errors="ignore"
                ).count("\nclass ")
        else:
            res.logs.append(f"il2cppdumper failed: {txt[-300:]}")

    def _il2cpp_binary(self, profile: GameProfile) -> str | None:
        root = Path(profile.root)
        for cand in root.rglob("GameAssembly.dll"):
            return str(cand)
        for cand in root.rglob("*.exe"):
            return str(cand)
        return None

    # -- native -----------------------------------------------------------
    def _disassemble_native(self, profile: GameProfile, tools: dict[str, str],
                            res: DecompileResult) -> None:
        native = None
        for b in sorted(profile.binaries, key=lambda b: -b.size):
            if b.kind == "pe" and b.size > 1024:
                p = Path(profile.root) / b.path
                if p.is_file():
                    native = p
                    break
        if native is None:
            return
        out = self.out_dir / "native"
        out.mkdir(parents=True, exist_ok=True)
        asm = out / (native.stem + ".asm")
        rc, txt = _run([tools["objdump"], "-d", "-M", "intel", str(native)], timeout=900)
        res.commands.append(f"objdump -d {native}")
        if rc == 0 and txt:
            asm.write_text(txt, encoding="utf-8", errors="ignore")
            res.tools_used.append("objdump")
            res.outputs["native_asm"] = str(asm)
            res.recovered["asm_lines"] = txt.count("\n")
            res.recovered["functions"] = txt.count(">:\n")
        else:
            res.logs.append(f"objdump failed: {txt[-300:]}")


def summarize(profile: GameProfile) -> dict:
    """Cheap, tool-free inventory used when no decompiler is installed."""
    inv = {"binaries": len(profile.binaries), "managed": 0, "native": 0, "total_bytes": 0}
    for b in profile.binaries:
        if b.kind == "pe" and not b.imports and b.size > 4096:
            inv["managed"] += 1
        elif b.kind in ("pe", "elf"):
            inv["native"] += 1
        inv["total_bytes"] += b.size
    return inv
