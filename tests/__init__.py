"""Test-suite configuration.

Tests build real PE/ELF fixtures with mingw-w64 / aarch64-linux-gnu-gcc once and
share them via :func:`fixtures`.  The suite is skipped gracefully when those
cross compilers are absent.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_FIXTURES: dict[str, Path] | None = None
_TMP: tempfile.TemporaryDirectory | None = None


def have_cross_compilers() -> bool:
    return bool(shutil.which("x86_64-w64-mingw32-gcc") and shutil.which("aarch64-linux-gnu-gcc"))


def have_cc() -> bool:
    return bool(shutil.which("cc") or shutil.which("gcc") or shutil.which("clang"))


def fixtures() -> dict[str, Path]:
    global _FIXTURES, _TMP
    if _FIXTURES is None:
        from tests import fixtures_builder

        _TMP = tempfile.TemporaryDirectory(prefix="portagent-fx-")
        built = fixtures_builder.build_all(_TMP.name)
        _FIXTURES = {f.name: f.path for f in built}
    return _FIXTURES
