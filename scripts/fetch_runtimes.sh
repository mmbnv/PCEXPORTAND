#!/usr/bin/env bash
# Fetch prebuilt ARM64 runtime payloads that the ported app needs at run time.
#
# PortAgent itself only *generates* the app + the compatibility layer.  Running a
# native x86/x64 Windows game on ARM64 additionally needs heavy prebuilt runtimes
# (Box64, Wine, DXVK).  Those are not vendored in this repo; this script pulls
# them into ~/.portagent-tools/runtime/<class>/ so the packager can bundle them
# into the APK assets.
#
# Usage:  scripts/fetch_runtimes.sh [class ...]
#   classes: engine_native mono_managed dbt_box64 wine_dxvk   (default: all)
#
# Nothing here runs automatically: review the licences of each component
# (Box64 = MIT, Wine = LGPL, DXVK = zlib) before redistributing a port.
set -uo pipefail

RUNTIME_HOME="${PAGENT_RUNTIME_HOME:-$HOME/.portagent-tools/runtime}"
CLASSES=("$@")
[ ${#CLASSES[@]} -eq 0 ] && CLASSES=(dbt_box64 wine_dxvk)
log() { echo "[runtimes] $*"; }

mkdir -p "$RUNTIME_HOME"

fetch_box64() {
  # Box64 ships ARM64 .so / binary builds; grab the latest release tarball.
  local dest="$RUNTIME_HOME/dbt_box64"
  [ -d "$dest" ] && { log "box64 already present"; return; }
  log "fetching Box64 (ARM64) ..."
  mkdir -p "$dest"
  local url="https://github.com/ptitSeb/box64/releases/latest/download/box64-android-arm64.tar.gz"
  if curl -fsSL "$url" -o /tmp/box64.tgz 2>/dev/null; then
    tar -xzf /tmp/box64.tgz -C "$dest" && log "box64 -> $dest"
  else
    log "box64 release not fetched automatically."
    log "  build it (https://github.com/ptitSeb/box64) and drop libbox64.so into $dest"
  fi
}

fetch_wine() {
  local dest="$RUNTIME_HOME/wine_dxvk"
  [ -d "$dest" ] && { log "wine payload already present"; return; }
  log "Wine + DXVK must be built for ARM64 (e.g. Winlator's build)."
  log "  drop libbox64.so, libwine.so, wine, and the DXVK .dll set into $dest"
  mkdir -p "$dest"
}

fetch_mono() {
  local dest="$RUNTIME_HOME/mono_managed"
  [ -d "$dest" ] && { log "mono payload already present"; return; }
  mkdir -p "$dest"
  log "Mono: copy libmonosgen-2.0.so + libmono-native.so (ARM64) into $dest"
}

for c in "${CLASSES[@]}"; do
  case "$c" in
    dbt_box64)    fetch_box64 ;;
    wine_dxvk)    fetch_wine ;;
    mono_managed) fetch_mono ;;
    engine_native) log "engine_native: the engine ships its own ARM64 libs with the game" ;;
    *) log "unknown runtime class: $c" ;;
  esac
done

log "done. Re-run 'portagent port <game>' to bundle whatever is installed."
