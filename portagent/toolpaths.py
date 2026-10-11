"""Resolve where external tools (Android SDK, jadx, runtime stack) live.

Priority, highest first:

1. explicit environment variables (``PAGENT_TOOLS_HOME``, ``ANDROID_HOME``,
   ``ANDROID_SDK_ROOT``, ``ANDROID_NDK_HOME``);
2. a ``tools/`` directory next to the repository root -- the *one-folder
   bundle* produced by ``scripts/make_bundle.sh``;
3. ``~/.portagent-tools`` -- the classic fetch location.

Keeping this in one place means the bundled build works with no environment
setup at all, while an explicit env var still overrides everything.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def is_valid_java_home(path: str | os.PathLike | None) -> bool:
    """True when ``path`` is a real JDK home (contains ``bin/javac``).

    Guards against Windows' ``.../Oracle/Java/javapath`` shim directory, which
    puts ``javac`` on PATH without being a JDK home. Passing such a path as
    ``JAVA_HOME`` makes ``sdkmanager`` abort.
    """
    if not path:
        return False
    p = Path(path)
    if not p.is_dir():
        return False
    exe = "javac.exe" if os.name == "nt" else "javac"
    return (p / "bin" / exe).is_file()


def tools_home() -> Path:
    """Root holding ``runtime/``, ``jadx/``, ``il2cppdumper/`` and friends.

    Order: explicit ``PAGENT_TOOLS_HOME`` -> the ``tools/`` folder of the
    one-folder bundle -> the classic ``~/.portagent-tools`` cache.
    """
    env = os.environ.get("PAGENT_TOOLS_HOME")
    if env:
        return Path(env)
    bundled = _REPO_ROOT / "tools"
    if bundled.is_dir():
        return bundled
    return Path.home() / ".portagent-tools"


def _load_bootstrap_env() -> None:
    """Import the env a prior ``portagent bootstrap`` recorded, if any.

    Without this, the SDK/NDK a user just downloaded would be invisible to the
    very next command, so they would have to hand-copy ``setx`` lines. Explicit
    variables in the current shell win, except an invalid ``JAVA_HOME`` (e.g.
    Windows' ``javapath`` shim parent), which the bootstrapped value overrides.
    """
    env_file = tools_home() / "env.json"
    if not env_file.is_file():
        return
    try:
        data = json.loads(env_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for key, value in data.items():
        if not (key.startswith(("ANDROID_", "JAVA_HOME", "PAGENT_TOOLS_HOME")) and value):
            continue
        # A stale/invalid JAVA_HOME in the shell must not shadow the JDK we
        # just bootstrapped; everything else respects an explicit override.
        if key == "JAVA_HOME" and not is_valid_java_home(os.environ.get("JAVA_HOME")):
            os.environ[key] = str(value)
        else:
            os.environ.setdefault(key, str(value))


def android_home() -> str | None:
    """Android SDK root: env first, then ``<tools_home>/android-sdk``."""
    _load_bootstrap_env()
    for var in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(var):
            return os.environ[var]
    cand = tools_home() / "android-sdk"
    if cand.is_dir():
        return str(cand)
    return None


def android_ndk_home() -> str | None:
    """Android NDK root, if one is present."""
    for var in ("ANDROID_NDK_HOME", "ANDROID_NDK_ROOT"):
        if os.environ.get(var):
            return os.environ[var]
    sdk = android_home()
    if not sdk:
        return None
    ndk_root = Path(sdk) / "ndk"
    if ndk_root.is_dir():
        for d in sorted(ndk_root.iterdir(), reverse=True):
            if (d / "toolchains/llvm/prebuilt").is_dir():
                return str(d)
    return None
