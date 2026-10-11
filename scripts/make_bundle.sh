#!/usr/bin/env bash
# Build a single self-contained folder with the code AND every tool the porter
# needs, so a user can move one directory to another machine and just run it.
#
#   bash scripts/make_bundle.sh [OUT_DIR]
#
# OUT_DIR defaults to dist/portagent-bundle.  Sources are taken from the classic
# cache (~/.portagent-tools) and from the repo itself.  Missing tools are
# skipped with a warning rather than failing the whole bundle.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$REPO_ROOT/dist/portagent-bundle}"
TOOLS_HOME="${PAGENT_TOOLS_HOME:-$HOME/.portagent-tools}"

say() { printf '\033[36m[bundle]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[bundle] WARN:\033[0m %s\n' "$*" >&2; }

say "repo:        $REPO_ROOT"
say "output:      $OUT_DIR"
say "tools cache: $TOOLS_HOME"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

# --- 1. code -----------------------------------------------------------------
say "copying application code ..."
mkdir -p "$OUT_DIR/portagent"
cp -a "$REPO_ROOT/portagent/." "$OUT_DIR/portagent/"
rm -rf "$OUT_DIR/portagent/__pycache__"
cp -a "$REPO_ROOT/run.py" "$OUT_DIR/"
cp -a "$REPO_ROOT/pyproject.toml" "$OUT_DIR/"
cp -a "$REPO_ROOT/README.md" "$OUT_DIR/" 2>/dev/null || true
cp -a "$REPO_ROOT/docs" "$OUT_DIR/" 2>/dev/null || true
mkdir -p "$OUT_DIR/scripts"
cp -a "$REPO_ROOT/scripts/." "$OUT_DIR/scripts/"
rm -rf "$OUT_DIR/scripts/__pycache__"

# --- 2. tools ----------------------------------------------------------------
mkdir -p "$OUT_DIR/tools"

copy_tool() {  # copy_tool <src> <dest-name>
    local src="$1" dest="$2"
    if [ -e "$src" ]; then
        say "bundling tool: $dest"
        cp -a "$src" "$OUT_DIR/tools/$dest"
    else
        warn "tool not found, skipping: $src"
    fi
}

copy_tool "$TOOLS_HOME/android-sdk"   "android-sdk"
copy_tool "$TOOLS_HOME/jadx"          "jadx"
copy_tool "$TOOLS_HOME/il2cppdumper"  "il2cppdumper"
copy_tool "$TOOLS_HOME/runtime"       "runtime"
copy_tool "$TOOLS_HOME/apktool.jar"   "apktool.jar"

# --- 3. installers + docs ----------------------------------------------------
say "writing installers ..."
cp -a "$REPO_ROOT/scripts/bundle/install.sh"    "$OUT_DIR/install.sh"
cp -a "$REPO_ROOT/scripts/bundle/START.sh"      "$OUT_DIR/START.sh"
cp -a "$REPO_ROOT/scripts/bundle/install.bat"   "$OUT_DIR/install.bat" 2>/dev/null || true
cp -a "$REPO_ROOT/scripts/bundle/START.bat"     "$OUT_DIR/START.bat"   2>/dev/null || true
cp -a "$REPO_ROOT/scripts/bundle/README-BUNDLE.md" "$OUT_DIR/README-BUNDLE.md" 2>/dev/null || true
chmod +x "$OUT_DIR/install.sh" "$OUT_DIR/START.sh" 2>/dev/null || true

# --- 4. optional single archive ---------------------------------------------
if [ "${MAKE_ARCHIVE:-0}" = "1" ]; then
    say "creating archive (this takes a while for ~3 GB) ..."
    ( cd "$(dirname "$OUT_DIR")" && tar czf "portagent-bundle.tar.gz" "$(basename "$OUT_DIR")" )
    say "archive: $(dirname "$OUT_DIR")/portagent-bundle.tar.gz"
fi

say "done."
say "size: $(du -sh "$OUT_DIR" | cut -f1)"
say "next: cd '$OUT_DIR' && ./install.sh && ./START.sh"
