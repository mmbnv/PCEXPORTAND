#!/usr/bin/env bash
# Fetch the ARM64 runtime stack the ported app needs at run time.
#
# Running a source-less x86/x64 Windows game on ARM64 needs heavy prebuilt
# runtimes: Box64 (x86/x64 -> ARM64), Wine (Win32), DXVK/VKD3D (D3D -> Vulkan)
# and a Vulkan driver (Turnip).  None of these are vendored in this repo.
#
# This is a thin wrapper around the Python fetcher, which is the single source
# of truth: it downloads a *pinned* Winlator release, verifies the APK SHA-256
# and the architecture of every binary, and unpacks the stack into
# ~/.portagent-tools/runtime/wine_dxvk/.
#
# Usage:
#   scripts/fetch_runtimes.sh            # fetch (idempotent)
#   scripts/fetch_runtimes.sh --force    # re-unpack
#   scripts/fetch_runtimes.sh --info     # show what is installed
#
# Licences: Box64 = MIT, Wine = LGPL-2.1, DXVK = zlib, Mesa/Turnip = MIT.
# Review them before redistributing a port.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "error: python3 not found" >&2
  exit 1
fi

exec "$PY" -m portagent.runtimes "$@"
