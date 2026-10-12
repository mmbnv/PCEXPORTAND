"""Self-contained Android build-toolchain bootstrap (pure stdlib, cross-platform).

The old shell/PowerShell scripts relied on ``apt``/``winget``/``choco`` being
present.  On a stock Windows box that is not a given, so "install the toolchain
and re-run" was a dead end.  This module downloads and unpacks everything with
nothing but the Python standard library:

* a JDK (Eclipse Temurin 17, per-OS/arch),
* the Android command-line tools (per-OS),
* then drives ``sdkmanager`` to install platform-tools, build-tools,
  ``platforms;android-34`` and the NDK.

Everything lands under the tools home (``PAGENT_TOOLS_HOME`` or
``~/.portagent-tools``) so it is disposable and does not touch the system.
Re-running only fills gaps.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .toolpaths import is_valid_java_home as _is_valid_java_home
from .toolpaths import tools_home

# Pinned, known-good versions.  Bumping these is a deliberate change.
CMDLINE_TOOLS_VERSION = "11076708"
BUILD_TOOLS_VERSION = "34.0.0"
PLATFORM_VERSION = "android-34"
NDK_VERSION = "26.1.10909125"
JDK_FEATURE = "17"

_CMDLINE_OS = {"windows": "win", "linux": "linux", "darwin": "mac"}
_JDK_OS = {"windows": "windows", "linux": "linux", "darwin": "mac"}


@dataclass
class BootstrapResult:
    ok: bool = False
    tools_home: str = ""
    java_home: str | None = None
    android_home: str | None = None
    ndk_home: str | None = None
    steps: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "tools_home": self.tools_home,
            "java_home": self.java_home,
            "android_home": self.android_home,
            "ndk_home": self.ndk_home,
            "steps": self.steps,
            "env": self.env,
            "logs": self.logs[-40:],
            "message": self.message,
        }


def _emit(progress, stage: str, msg: str) -> None:
    if progress is not None:
        try:
            progress(stage, msg)
        except Exception:
            pass


def _download(url: str, dest: Path, progress=None, label: str = "") -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    _emit(progress, "download", f"{label or url}")
    req = urllib.request.Request(url, headers={"User-Agent": "portagent-bootstrap"})
    with urllib.request.urlopen(req, timeout=300) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out, 1 << 20)
    tmp.replace(dest)


def _unzip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
        # zipfile does not restore the Unix executable bit, which makes the
        # sdkmanager shell script (and other bin/ launchers) unrunnable.
        if os.name != "nt":
            for info in zf.infolist():
                mode = (info.external_attr >> 16) & 0o777
                if mode & 0o111:
                    p = dest / info.filename
                    if p.is_file():
                        p.chmod(p.stat().st_mode | 0o755)


def _extract_tar(archive: Path, dest: Path) -> None:
    import tarfile

    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest, filter="data")


def _extract_archive(archive: Path, dest: Path) -> None:
    """Unpack a zip/gzip/tar archive, choosing the reader by magic bytes.

    The caller names the cached file ``jdk.zip`` regardless of the real format,
    and the Adoptium API redirects ``/jdk/17/...`` to a ``.tar.gz`` — so trusting
    the suffix makes ``zipfile`` choke with "File is not a zip file".
    """
    with archive.open("rb") as fh:
        magic = fh.read(4)
    if magic[:2] == b"\x1f\x8b" or magic[:2] == b"BZ" or magic[:2] == b"\xfd7":
        _extract_tar(archive, dest)
    elif magic[:2] == b"PK":
        _unzip(archive, dest)
    else:
        raise RuntimeError(f"unrecognized archive format for {archive}")


# ---------------------------------------------------------------------------
# JDK
# ---------------------------------------------------------------------------
def _jdk_url() -> str:
    os_name = _JDK_OS.get(platform.system().lower())
    if not os_name:
        raise RuntimeError(f"unsupported OS for JDK download: {platform.system()}")
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x64"
    return (
        f"https://api.adoptium.net/v3/binary/latest/{JDK_FEATURE}/ga/"
        f"{os_name}/{arch}/jdk/hotspot/normal/eclipse"
    )


def _find_java_home(root: Path) -> Path | None:
    """Locate a ``javac`` inside an unpacked JDK tree."""
    if not root.is_dir():
        return None
    exe = "javac.exe" if os.name == "nt" else "javac"
    for cand in root.rglob(exe):
        if cand.parent.name == "bin":
            return cand.parent.parent
    return None


def ensure_jdk(home: Path, progress=None) -> Path | None:
    """Return a usable JDK home, downloading Temurin if none is valid.

    A JDK is only trusted when it actually looks like a JDK home; a stale or
    shim ``JAVA_HOME``/``javac`` is ignored rather than passed to sdkmanager,
    which would abort with "JAVA_HOME is set to an invalid directory".
    """
    existing = _find_java_home(home / "jdk")
    if existing is not None and _is_valid_java_home(existing):
        return existing

    env_home = os.environ.get("JAVA_HOME")
    if _is_valid_java_home(env_home):
        return Path(env_home)

    javac = shutil.which("javac")
    if javac:
        # Follow symlinks/shim launchers, then also try the literal location.
        for cand in (Path(javac).resolve().parent.parent, Path(javac).parent.parent):
            if _is_valid_java_home(cand):
                return cand
        _emit(progress, "jdk",
              f"ignoring javac at {javac}: its parent is not a valid JDK home")

    url = _jdk_url()
    archive = home / "cache" / "jdk.zip"
    if not archive.is_file():
        _download(url, archive, progress, label=f"JDK {JDK_FEATURE} (Temurin)")
    dest = home / "jdk"
    _extract_archive(archive, dest)
    found = _find_java_home(dest)
    if found is None:
        raise RuntimeError("no javac found after unpacking the JDK archive")
    return found


# ---------------------------------------------------------------------------
# Android SDK
# ---------------------------------------------------------------------------
def _cmdline_tools_url() -> str:
    os_name = _CMDLINE_OS.get(platform.system().lower())
    if not os_name:
        raise RuntimeError(f"unsupported OS: {platform.system()}")
    return (
        "https://dl.google.com/android/repository/"
        f"commandlinetools-{os_name}-{CMDLINE_TOOLS_VERSION}_latest.zip"
    )


def _sdkmanager(sdk: Path) -> Path | None:
    base = sdk / "cmdline-tools" / "latest" / "bin"
    for name in ("sdkmanager.bat", "sdkmanager"):
        p = base / name
        if p.is_file():
            return p
    return None


def _run_sdkmanager(sm: Path, sdk: Path, packages: list[str], progress=None,
                    java_home: Path | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    if java_home is not None:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    if os.name == "nt" and sm.suffix.lower() in (".bat", ".cmd"):
        cmd = ["cmd", "/c", str(sm)]
    else:
        cmd = [str(sm)]
    cmd += [f"--sdk_root={sdk}", *packages]
    _emit(progress, "sdkmanager", " ".join(packages))
    try:
        p = subprocess.run(
            cmd, input="y\n" * 40, capture_output=True, text=True,
            env=env, timeout=3600,
        )
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "sdkmanager timed out"


def ensure_android_sdk(home: Path, progress=None, java_home: Path | None = None) -> Path:
    """Download cmdline-tools and install the SDK packages we need."""
    sdk = home / "android-sdk"
    sm = _sdkmanager(sdk)
    if sm is None:
        archive = home / "cache" / "cmdline-tools.zip"
        _download(_cmdline_tools_url(), archive, progress, label="Android cmdline-tools")
        tmp = home / "cache" / "cmdline-tools-extract"
        _unzip(archive, tmp)
        (sdk / "cmdline-tools").mkdir(parents=True, exist_ok=True)
        src = tmp / "cmdline-tools"
        dest = sdk / "cmdline-tools" / "latest"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(src), str(dest))
        sm = _sdkmanager(sdk)
    if sm is None:
        raise RuntimeError("sdkmanager not found after unpacking cmdline-tools")
    _run_sdkmanager(sm, sdk, ["--licenses"], progress, java_home)
    rc, out = _run_sdkmanager(
        sm, sdk,
        ["platform-tools", f"build-tools;{BUILD_TOOLS_VERSION}",
         f"platforms;{PLATFORM_VERSION}"],
        progress, java_home,
    )
    if rc != 0:
        raise RuntimeError(f"sdkmanager failed ({rc}): {out[-1500:]}")
    rc, out = _run_sdkmanager(sm, sdk, [f"ndk;{NDK_VERSION}"], progress, java_home)
    if rc != 0:
        # Fall back to whatever NDK the channel lists.
        rc2, out2 = _run_sdkmanager(sm, sdk, ["ndk"], progress, java_home)
        if rc2 != 0:
            raise RuntimeError(f"NDK install failed ({rc}): {out[-800:]} / {out2[-800:]}")
    return sdk


def _find_ndk(sdk: Path) -> Path | None:
    root = sdk / "ndk"
    if not root.is_dir():
        return None
    for d in sorted(root.iterdir(), reverse=True):
        if (d / "toolchains" / "llvm" / "prebuilt").is_dir():
            return d
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def bootstrap(progress=None, home: Path | None = None) -> BootstrapResult:
    """Install JDK + Android SDK/NDK under the tools home. Idempotent."""
    home = Path(home) if home else tools_home()
    home.mkdir(parents=True, exist_ok=True)
    res = BootstrapResult(tools_home=str(home))

    try:
        jdk = ensure_jdk(home, progress)
    except Exception as exc:  # noqa: BLE001
        res.steps["jdk"] = "fail"
        res.message = f"JDK bootstrap failed: {exc}"
        return res
    res.steps["jdk"] = "ok" if jdk else "fail"
    res.java_home = str(jdk) if jdk else None

    try:
        sdk = ensure_android_sdk(home, progress, jdk)
    except Exception as exc:  # noqa: BLE001
        res.steps["android-sdk"] = "fail"
        res.message = f"Android SDK bootstrap failed: {exc}"
        return res
    res.android_home = str(sdk)
    res.steps["android-sdk"] = "ok"

    ndk = _find_ndk(sdk)
    res.ndk_home = str(ndk) if ndk else None
    res.steps["ndk"] = "ok" if ndk else "missing"

    res.env = {
        "PAGENT_TOOLS_HOME": str(home),
        "ANDROID_HOME": str(sdk),
        "ANDROID_SDK_ROOT": str(sdk),
    }
    if jdk:
        res.env["JAVA_HOME"] = str(jdk)
    if ndk:
        res.env["ANDROID_NDK_HOME"] = str(ndk)
        res.env["ANDROID_NDK_ROOT"] = str(ndk)

    res.ok = jdk is not None and ndk is not None
    res.message = (
        "toolchain ready" if res.ok
        else "toolchain partially installed; see steps"
    )
    if res.env:
        # Persist so the *next* command finds the SDK without any setx/exports.
        try:
            (home / "env.json").write_text(
                json.dumps(res.env, indent=2), encoding="utf-8"
            )
        except OSError as exc:  # noqa: BLE001
            res.logs.append(f"could not write env.json: {exc}")
    _emit(progress, "bootstrap", res.message)
    return res


def apply_env(res: BootstrapResult) -> None:
    """Export the result into this process so the packager sees it immediately."""
    for k, v in res.env.items():
        os.environ[k] = v
    if res.java_home:
        os.environ["PATH"] = (
            str(Path(res.java_home) / "bin") + os.pathsep + os.environ.get("PATH", "")
        )
