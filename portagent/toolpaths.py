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

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


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


def android_home() -> str | None:
    """Android SDK root: env first, then ``<tools_home>/android-sdk``."""
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
