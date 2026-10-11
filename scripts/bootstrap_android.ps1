# Bootstrap the Android build toolchain for PortAgent on Windows.
#
# Installs (under $env:PAGENT_TOOLS, default: %USERPROFILE%\.portagent-tools):
#   * a JDK (via winget/choco if available)
#   * Android command-line tools
#   * platform-tools, build-tools, platforms;android-34, NDK, cmake
#
# It then prints the environment variables to set. Re-running only fills gaps.
#
# Usage (from the repo root, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_android.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_android.ps1 -Full
#
# -Full additionally downloads jadx, apktool and Il2CppDumper.

param(
    [switch]$Full
)

$ErrorActionPreference = "Stop"
$Tools      = if ($env:PAGENT_TOOLS) { $env:PAGENT_TOOLS } else { Join-Path $env:USERPROFILE ".portagent-tools" }
$AndroidSdk = Join-Path $Tools "android-sdk"
$NdkVersion = if ($env:NDK_VERSION) { $env:NDK_VERSION } else { "26.1.10909125" }
$BtVersion  = if ($env:BT_VERSION) { $env:BT_VERSION } else { "34.0.0" }
$Platform   = "android-34"
$CmdlineUrl = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"

function Log($msg) { Write-Host "[bootstrap] $msg" }
function Have($cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }

New-Item -ItemType Directory -Force -Path $Tools | Out-Null

# --- JDK -------------------------------------------------------------------
if (-not (Have "javac")) {
    Log "no JDK found; trying winget/choco ..."
    if (Have "winget") {
        try { winget install --silent --accept-source-agreements --accept-package-agreements Microsoft.OpenJDK.17 } catch { Log "winget install failed: $_" }
    } elseif (Have "choco") {
        try { choco install -y openjdk17 } catch { Log "choco install failed: $_" }
    }
    if (-not (Have "javac")) {
        Log "Please install a JDK 17 manually (https://adoptium.net) and re-run."
    }
}

# --- Android command-line tools -------------------------------------------
$cmdline = Join-Path $AndroidSdk "cmdline-tools\latest"
if (-not (Test-Path $cmdline)) {
    Log "downloading Android command-line tools ..."
    $tmp = Join-Path $env:TEMP ("pag_cmdline_" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "cmdline.zip"
    Invoke-WebRequest -Uri $CmdlineUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $AndroidSdk "cmdline-tools") | Out-Null
    Move-Item (Join-Path $tmp "cmdline-tools") $cmdline
    Remove-Item -Recurse -Force $tmp
}

$env:ANDROID_HOME = $AndroidSdk
$env:ANDROID_SDK_ROOT = $AndroidSdk
$sdkmanager = Join-Path $cmdline "bin\sdkmanager.bat"

if (Test-Path $sdkmanager) {
    Log "accepting licenses ..."
    # 'y' answers for each license prompt.
    $yes = ("y`n" * 20)
    $yes | & $sdkmanager --sdk_root="$AndroidSdk" --licenses 2>&1 | Out-Null
    Log "installing platform-tools, build-tools:$BtVersion, platforms;$Platform, ndk, cmake ..."
    & $sdkmanager --sdk_root="$AndroidSdk" "platform-tools" "build-tools;$BtVersion" `
        "platforms;$Platform" "cmake;3.22.1" 2>&1 | Out-Null
    if (-not (& $sdkmanager --sdk_root="$AndroidSdk" "ndk;$NdkVersion" 2>&1 | Out-Null; Test-Path (Join-Path $AndroidSdk "ndk\$NdkVersion"))) {
        Log "requested NDK $NdkVersion not available; listing ..."
        & $sdkmanager --sdk_root="$AndroidSdk" --list 2>&1 | Select-String "^\s*ndk;" | Select-Object -Last 1
    }
} else {
    Log "sdkmanager not found at $sdkmanager"
}

# --- optional decompilers --------------------------------------------------
if ($Full) {
    if (-not (Test-Path (Join-Path $Tools "jadx"))) {
        Log "downloading jadx ..."
        $j = Join-Path $Tools "jadx.zip"
        Invoke-WebRequest -Uri "https://github.com/skylot/jadx/releases/download/v1.5.1/jadx-1.5.1.zip" -OutFile $j
        Expand-Archive -Path $j -DestinationPath (Join-Path $Tools "jadx") -Force
    }
    if (-not (Test-Path (Join-Path $Tools "apktool.jar"))) {
        Log "downloading apktool ..."
        Invoke-WebRequest -Uri "https://github.com/iBotPeaches/Apktool/releases/download/v2.11.1/apktool_2.11.1.jar" -OutFile (Join-Path $Tools "apktool.jar")
    }
    if (-not (Test-Path (Join-Path $Tools "Il2CppDumper"))) {
        Log "downloading Il2CppDumper ..."
        $z = Join-Path $Tools "il2cppdumper.zip"
        Invoke-WebRequest -Uri "https://github.com/Perfare/Il2CppDumper/releases/download/v6.7.46/Il2CppDumper-net6-v6.7.46.zip" -OutFile $z
        Expand-Archive -Path $z -DestinationPath (Join-Path $Tools "Il2CppDumper") -Force
    }
}

# --- summary ---------------------------------------------------------------
$ndkDir = Join-Path $AndroidSdk "ndk"
Log "done. Android SDK at: $AndroidSdk"
Write-Host ""
Write-Host "Set these in your current shell (or System Properties -> Environment Variables):" -ForegroundColor Cyan
Write-Host "  `$env:ANDROID_HOME = `"$AndroidSdk`""
Write-Host "  `$env:ANDROID_SDK_ROOT = `"$AndroidSdk`""
if (Test-Path $ndkDir) {
    $ndk = (Get-ChildItem $ndkDir -Directory | Select-Object -Last 1).FullName
    Write-Host "  `$env:ANDROID_NDK_HOME = `"$ndk`""
}
Write-Host ""
Write-Host "To persist them for future sessions:" -ForegroundColor Cyan
Write-Host "  setx ANDROID_HOME `"$AndroidSdk`""
Write-Host "  setx ANDROID_SDK_ROOT `"$AndroidSdk`""
Write-Host ""
Write-Host "Then build:  python run.py   (choose the game folder, click Build APK)"
