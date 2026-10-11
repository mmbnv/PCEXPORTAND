#!/usr/bin/env bash
# Bootstrap the reverse-engineering + Android build toolchain used by PortAgent.
#
# Everything installs under $PAGENT_TOOLS (default: ~/.portagent-tools) so the
# container stays clean and the environment is reproducible.  The script is
# idempotent: re-running only fills in what is missing.
#
# Usage:  scripts/bootstrap_android.sh [--full]
#   default : JDK + Android SDK (cmdline-tools, platform-tools, build-tools) +
#             NDK + CMake + Gradle  (enough to build a real APK)
#   --full  : additionally installs Ghidra, jadx, apktool, Il2CppDumper, Box64
set -uo pipefail

TOOLS="${PAGENT_TOOLS:-$HOME/.portagent-tools}"
ANDROID_SDK="$TOOLS/android-sdk"
NDK_VERSION="${NDK_VERSION:-26.1.10909125}"
BT_VERSION="${BT_VERSION:-34.0.0}"
PLATFORM="android-34"
GRADLE_VERSION="${GRADLE_VERSION:-8.7}"
CMDLINE_ZIP_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
FULL=0
[ "${1:-}" = "--full" ] && FULL=1

mkdir -p "$TOOLS"
log() { echo "[bootstrap] $*"; }

need_cmd() { command -v "$1" >/dev/null 2>&1; }

# --- JDK 17 ---------------------------------------------------------------
if ! need_cmd java; then
  log "installing OpenJDK 17 ..."
  sudo apt-get update -qq && sudo apt-get install -y -qq openjdk-17-jdk-headless unzip >/dev/null
fi
export JAVA_HOME="${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")}"
log "JAVA_HOME=$JAVA_HOME"

# --- base utilities -------------------------------------------------------
for pkg in unzip zip cmake ninja-build pkg-config; do
  if ! need_cmd "${pkg%%-*}"; then sudo apt-get install -y -qq "$pkg" >/dev/null 2>&1 || true; fi
done

# --- Android SDK ----------------------------------------------------------
if [ ! -d "$ANDROID_SDK/cmdline-tools/latest" ]; then
  log "installing Android command-line tools ..."
  mkdir -p "$ANDROID_SDK/cmdline-tools"
  tmp="$(mktemp -d)"
  curl -fsSL "$CMDLINE_ZIP_URL" -o "$tmp/cmdline.zip"
  unzip -q "$tmp/cmdline.zip" -d "$tmp"
  mv "$tmp/cmdline-tools" "$ANDROID_SDK/cmdline-tools/latest"
  rm -rf "$tmp"
fi
export ANDROID_HOME="$ANDROID_SDK"
export ANDROID_SDK_ROOT="$ANDROID_SDK"
SDKMANAGER="$ANDROID_SDK/cmdline-tools/latest/bin/sdkmanager"
if [ -x "$SDKMANAGER" ]; then
  yes | "$SDKMANAGER" --sdk_root="$ANDROID_SDK" --licenses >/dev/null 2>&1 || true
  log "installing platform-tools, build-tools:$BT_VERSION, platforms;$PLATFORM, ndk;$NDK_VERSION, cmake ..."
  "$SDKMANAGER" --sdk_root="$ANDROID_SDK" \
    "platform-tools" "build-tools;$BT_VERSION" "platforms;$PLATFORM" \
    "ndk;$NDK_VERSION" "cmake;3.22.1" >/dev/null
fi
export ANDROID_NDK_HOME="$ANDROID_SDK/ndk/$NDK_VERSION"
export ANDROID_NDK_ROOT="$ANDROID_NDK_HOME"

# --- Gradle ---------------------------------------------------------------
if ! need_cmd gradle; then
  log "installing Gradle $GRADLE_VERSION ..."
  tmp="$(mktemp -d)"
  curl -fsSL "https://services.gradle.org/distributions/gradle-$GRADLE_VERSION-bin.zip" -o "$tmp/g.zip"
  unzip -q "$tmp/g.zip" -d "$TOOLS"
  rm -rf "$tmp"
  ln -sf "$TOOLS/gradle-$GRADLE_VERSION/bin/gradle" "$TOOLS/bin/gradle" 2>/dev/null || true
fi

if [ "$FULL" = "1" ]; then
  # --- Ghidra (headless decompiler) --------------------------------------
  if [ ! -d "$TOOLS/ghidra" ]; then
    log "installing Ghidra ..."
    tmp="$(mktemp -d)"
    curl -fsSL "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_11.2.1_build/ghidra_11.2.1_PUBLIC_20241105.zip" -o "$tmp/ghidra.zip" \
      && unzip -q "$tmp/ghidra.zip" -d "$TOOLS" && ln -sfn "$TOOLS/ghidra_11.2.1_PUBLIC" "$TOOLS/ghidra"
    rm -rf "$tmp"
  fi
  # --- jadx (DEX/APK -> Java) --------------------------------------------
  if [ ! -d "$TOOLS/jadx" ]; then
    log "installing jadx ..."
    curl -fsSL "https://github.com/skylot/jadx/releases/download/v1.5.1/jadx-1.5.1.zip" -o "$TOOLS/jadx.zip" \
      && unzip -q "$TOOLS/jadx.zip" -d "$TOOLS/jadx"
  fi
  # --- apktool ------------------------------------------------------------
  if [ ! -f "$TOOLS/bin/apktool.jar" ]; then
    log "installing apktool ..."
    mkdir -p "$TOOLS/bin"
    curl -fsSL "https://github.com/iBotPeaches/Apktool/releases/download/v2.11.1/apktool_2.11.1.jar" -o "$TOOLS/bin/apktool.jar"
    printf '#!/usr/bin/env bash\nexec java -jar "%s/bin/apktool.jar" "$@"\n' "$TOOLS" > "$TOOLS/bin/apktool"
    chmod +x "$TOOLS/bin/apktool"
  fi
  # --- Il2CppDumper -------------------------------------------------------
  if [ ! -d "$TOOLS/Il2CppDumper" ]; then
    log "installing Il2CppDumper ..."
    curl -fsSL "https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.46/Il2CppDumper-net6-v6.7.46.zip" -o "$TOOLS/il2cppdumper.zip" \
      && mkdir -p "$TOOLS/Il2CppDumper" && unzip -q "$TOOLS/il2cppdumper.zip" -d "$TOOLS/Il2CppDumper"
  fi
  log "note: Box64/Wine are installed via distro packages or built from source; see docs/TOOLCHAIN.md"
fi

cat <<EOF

[bootstrap] done.
Add to your shell:
  export ANDROID_HOME="$ANDROID_SDK"
  export ANDROID_NDK_HOME="$ANDROID_NDK_HOME"
  export PATH="\$PATH:$ANDROID_SDK/cmdline-tools/latest/bin:$ANDROID_SDK/platform-tools:$TOOLS/bin:$TOOLS/gradle-$GRADLE_VERSION/bin"

Then run:
  python -m portagent run /path/to/game --work-dir ./run
EOF
