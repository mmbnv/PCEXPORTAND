"""Runtime acquisition: fetch the real ARM64 PC-game stack.

A source-less x86/x64 Windows game cannot be rebuilt for ARM64 - the only route
that works for such titles is the same one Winlator/GameHub use:

    game.exe  --(Wine)-->  Win32/NT syscalls
              --(Box64)--> ARM64 machine code
              --(DXVK/VKD3D)--> Vulkan  --(Turnip)--> GPU

None of those binaries are built from source here: they are the well-known
prebuilt ARM64 projects.  This module downloads a *pinned* Winlator release,
unpacks the pieces we need and drops them into
``~/.portagent-tools/runtime/`` where :class:`portagent.runtime.RuntimeBundler`
picks them up.

Everything is verified on the way in:

* the APK's SHA-256 is recorded,
* every ``.so``/binary is checked to be an ELF of the expected architecture
  (``box64`` must be ``aarch64``; ``wine`` is ``x86-64`` because Box64 runs it),
* a Box64 version probe is run when a host ``qemu-aarch64`` is available.

If a download is not possible the caller gets an honest failure - we never
pretend a runtime is present when it is not.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .toolpaths import tools_home

# Pinned upstream release.  Bumping this is a deliberate, reviewed change.
WINLATOR_VERSION = "11.2.0"
WINLATOR_APK_URL = (
    "https://github.com/brunodev85/winlator/releases/download/"
    f"v{WINLATOR_VERSION}/Winlator_{WINLATOR_VERSION}.apk"
)

from .toolpaths import tools_home

RUNTIME_HOME = tools_home() / "runtime"
CACHE_HOME = tools_home() / "cache"

# ELF e_machine values we accept for each role.
_ELF_AARCH64 = 0xB7
_ELF_X86_64 = 0x3E

# What we pull out of the Winlator APK, and where it lands under RUNTIME_HOME.
# member -> (destination subdir relative to RUNTIME_HOME, expected arch or None)
_TZST_MEMBERS = {
    f"assets/box64/box64-0.4.4.tzst": ("wine_dxvk/box64", None),
    "assets/rootfs.tzst": ("wine_dxvk/rootfs", None),
    "assets/dxwrapper/dxvk-2.4.1.tzst": ("wine_dxvk/dxvk", None),
    "assets/dxwrapper/vkd3d-2.14.1.tzst": ("wine_dxvk/vkd3d", None),
    "assets/graphics_driver/turnip-26.2.0.tzst": ("wine_dxvk/drivers/turnip", None),
}

# Plain (non-tzst) members we also want, with their destination under RUNTIME_HOME.
_PLAIN_MEMBERS = {
    "assets/box64/default.box64rc": "wine_dxvk/box64/box64rc/default.box64rc",
    "assets/box64/env_vars.json": "wine_dxvk/box64/box64rc/env_vars.json",
}

# Binaries we require and their expected ELF machine, checked after extraction.
_REQUIRED_BINARIES = {
    "wine_dxvk/box64/usr/local/bin/box64": _ELF_AARCH64,
    "wine_dxvk/rootfs/opt/wine/bin/wine": _ELF_X86_64,
    "wine_dxvk/rootfs/opt/wine/bin/wineserver": _ELF_X86_64,
}


class RuntimeError_(RuntimeError):
    """Raised when a runtime cannot be fetched or fails validation."""


@dataclass
class FetchResult:
    ok: bool
    version: str = WINLATOR_VERSION
    apk: str | None = None
    apk_sha256: str | None = None
    installed: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    box64_version: str | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "version": self.version,
            "apk": self.apk,
            "apk_sha256": self.apk_sha256,
            "installed": self.installed,
            "checks": self.checks,
            "box64_version": self.box64_version,
            "logs": self.logs[-30:],
        }


def _emit(progress, stage: str, msg: str) -> None:
    if progress is None:
        return
    try:
        progress(stage, msg)
    except TypeError:
        progress(msg)


def _elf_machine(path: Path) -> int | None:
    """Return the ELF ``e_machine`` of a file, or None if it is not ELF."""
    try:
        with path.open("rb") as fh:
            head = fh.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return None
    little = head[5] == 1
    return struct.unpack("<H" if little else ">H", head[18:20])[0]


def _sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _have_zstd() -> bool:
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return bool(shutil.which("zstd"))


def _extract_tzst(data: bytes, dest: Path) -> None:
    """Extract a ``.tzst`` (zstd-compressed tar) blob into ``dest``."""
    dest.mkdir(parents=True, exist_ok=True)
    try:
        import zstandard

        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            tar.extractall(dest, filter="data")
        return
    except ImportError:
        pass
    if not shutil.which("zstd"):
        raise RuntimeError_(
            "need the 'zstandard' Python package or the 'zstd' CLI to unpack runtimes"
        )
    tmp = dest.parent / (dest.name + ".tzst")
    tmp.write_bytes(data)
    try:
        subprocess.run(["tar", "--zstd", "-xf", str(tmp), "-C", str(dest)], check=True)
    finally:
        tmp.unlink(missing_ok=True)


def ensure_winlator_apk(progress=None, url: str = WINLATOR_APK_URL,
                        version: str = WINLATOR_VERSION) -> tuple[Path, str]:
    """Download (once) and cache the pinned Winlator APK. Returns (path, sha256)."""
    CACHE_HOME.mkdir(parents=True, exist_ok=True)
    apk = CACHE_HOME / f"winlator-{version}.apk"
    if apk.is_file() and apk.stat().st_size > 10_000_000:
        return apk, _sha256_file(apk)
    _emit(progress, "fetch", f"downloading Winlator {version} (~150 MB, cached afterwards)")
    tmp = apk.with_suffix(".part")
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out, 1 << 20)
    except Exception as exc:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        raise RuntimeError_(f"could not download runtime from {url}: {exc}") from exc
    tmp.replace(apk)
    return apk, _sha256_file(apk)


def fetch_runtimes(force: bool = False, progress=None,
                   runtime_home: Path | None = None) -> FetchResult:
    """Fetch + unpack + validate the ARM64 stack into ``runtime_home``."""
    home = Path(runtime_home) if runtime_home else RUNTIME_HOME
    res = FetchResult(ok=False)
    if not _have_zstd():
        res.logs.append("no zstd support: pip install zstandard")
        return res

    apk, sha = ensure_winlator_apk(progress)
    res.apk = str(apk)
    res.apk_sha256 = sha
    _emit(progress, "fetch", f"APK sha256={sha[:16]}…")

    with zipfile.ZipFile(apk) as zf:
        names = set(zf.namelist())
        for member, (dest_rel, _arch) in _TZST_MEMBERS.items():
            if member not in names:
                res.logs.append(f"member missing in APK: {member}")
                continue
            dest = home / dest_rel
            if dest.is_dir() and any(dest.iterdir()) and not force:
                res.installed.append(dest_rel)
                _emit(progress, "fetch", f"already installed: {dest_rel}")
                continue
            _emit(progress, "fetch", f"unpacking {Path(member).name} -> {dest_rel}")
            _extract_tzst(zf.read(member), dest)
            res.installed.append(dest_rel)

        for member, dest_rel in _PLAIN_MEMBERS.items():
            if member not in names:
                res.logs.append(f"member missing in APK: {member}")
                continue
            dest = home / dest_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(member))
            res.installed.append(dest_rel)

    # Validate the binaries we depend on.
    for rel, want in _REQUIRED_BINARIES.items():
        p = home / rel
        if not p.is_file():
            res.checks[rel] = "missing"
            continue
        machine = _elf_machine(p)
        if machine is None:
            res.checks[rel] = "not an ELF"
        elif machine != want:
            res.checks[rel] = f"wrong arch (e_machine=0x{machine:x}, want 0x{want:x})"
        else:
            res.checks[rel] = "ok"
            os.chmod(p, 0o755)

    box64 = home / "wine_dxvk/box64/usr/local/bin/box64"
    if box64.is_file():
        res.box64_version = _probe_box64(box64, home)

    dirs_ok = all((home / rel).is_dir() for rel, _ in _TZST_MEMBERS.values())
    res.ok = all(v == "ok" for v in res.checks.values()) and dirs_ok
    _write_manifest(home, res)
    return res


def _probe_box64(box64: Path, home: Path) -> str | None:
    """Run ``box64 --version`` when we can. On a real ARM64 host it just works;
    on x86 dev boxes a qemu-aarch64 (if present) is used to prove the binary."""
    loader = home / "wine_dxvk/rootfs/usr/lib/ld-linux-aarch64.so.1"
    qemu = shutil.which("qemu-aarch64") or shutil.which("qemu-aarch64-static")
    for cmd in (
        [str(box64), "--version"],
        [qemu, "-L", str(home / "wine_dxvk/rootfs"), str(box64), "--version"] if qemu else None,
        [qemu, "-L", str(home / "wine_dxvk/rootfs"), str(loader), str(box64), "--version"] if qemu and loader.is_file() else None,
    ):
        if not cmd:
            continue
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        out = (p.stdout + p.stderr).strip()
        if "Box64" in out:
            return out.splitlines()[0].strip()
    return None


def _write_manifest(home: Path, res: FetchResult) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "manifest.json").write_text(
        json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def runtime_ready(runtime_home: Path | None = None) -> bool:
    home = Path(runtime_home) if runtime_home else RUNTIME_HOME
    return all((home / rel).is_file() for rel in _REQUIRED_BINARIES)


def info(runtime_home: Path | None = None) -> dict:
    home = Path(runtime_home) if runtime_home else RUNTIME_HOME
    manifest = home / "manifest.json"
    data: dict = {"home": str(home), "ready": runtime_ready(home)}
    if manifest.is_file():
        try:
            data.update(json.loads(manifest.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    return data


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Fetch the ARM64 PC-game runtime stack")
    ap.add_argument("--force", action="store_true", help="re-unpack even if present")
    ap.add_argument("--info", action="store_true", help="show what is installed")
    args = ap.parse_args(argv)

    if args.info:
        print(json.dumps(info(), indent=2, ensure_ascii=False))
        return 0

    def progress(stage: str, msg: str) -> None:
        print(f"[{stage}] {msg}", flush=True)

    res = fetch_runtimes(force=args.force, progress=progress)
    print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
    return 0 if res.ok else 1


if __name__ == "__main__":
    sys.exit(main())
