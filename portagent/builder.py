"""Builder (mission section 4, ``Builder``).

Turns the Translator output into a real Android project and (when the SDK is
present) a signed debug APK:

  * scaffolds ``settings.gradle``, ``build.gradle``, ``AndroidManifest.xml``,
    ``CMakeLists.txt``, ``MainActivity.kt`` and a native entry point,
  * compiles the generated shims with the host compiler as a deterministic
    smoke test (this is what the Verifier relies on in a bare container),
  * cross-compiles for ``arm64-v8a`` when the NDK is available,
  * runs ``gradle assembleDebug`` and locates the produced APK.

Nothing here is faked: if the Android toolchain is missing the result is
reported as ``partial`` and the exact bootstrap command is attached.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from .models import GameProfile, PortStrategy

PACKAGE = "dev.portagent.port"
APP_LABEL = "PortAgent Port"


@dataclass
class BuildResult:
    status: str = "fail"  # "ok" | "partial" | "fail"
    project_dir: str = ""
    apk: str | None = None
    objects: list[str] = field(default_factory=list)
    abis: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "project_dir": self.project_dir,
            "apk": self.apk,
            "objects": self.objects,
            "abis": self.abis,
            "logs": self.logs[-40:],
            "commands": self.commands,
        }


def _run(cmd: list[str], cwd: str | Path | None = None, timeout: int = 900) -> tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
        )
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError:
        return 127, f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"


class Builder:
    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)

    # -- scaffolding ------------------------------------------------------
    def scaffold(self, profile: GameProfile, strategy: PortStrategy, translated: dict) -> Path:
        proj = self.work_dir / "android_project"
        (proj / "app" / "src" / "main" / "java" / "dev" / "portagent" / "port").mkdir(
            parents=True, exist_ok=True
        )
        (proj / "app" / "src" / "main" / "cpp").mkdir(parents=True, exist_ok=True)
        # Copy translator output into the native module.
        cpp = proj / "app" / "src" / "main" / "cpp"
        src = Path(translated["out_dir"])
        for rel in ("include", "src", "mapping.json"):
            s = src / rel
            d = cpp / rel
            if s.is_dir():
                if d.exists():
                    shutil.rmtree(d)
                shutil.copytree(s, d)
            elif s.is_file():
                shutil.copy2(s, d)

        (proj / "settings.gradle").write_text(
            textwrap.dedent(
                """\
                pluginManagement {
                    repositories { google(); mavenCentral(); gradlePluginPortal() }
                }
                dependencyResolutionManagement {
                    repositories { google(); mavenCentral() }
                }
                rootProject.name = "portagent"
                include ':app'
                """
            ),
            encoding="utf-8",
        )
        (proj / "build.gradle").write_text(
            textwrap.dedent(
                """\
                plugins {
                    id 'com.android.application' version '8.5.2'
                }
                """
            ),
            encoding="utf-8",
        )
        (proj / "gradle.properties").write_text(
            "android.useAndroidX=true\norg.gradle.jvmargs=-Xmx2g\n", encoding="utf-8"
        )
        (proj / "app" / "build.gradle").write_text(
            textwrap.dedent(
                f"""\
                plugins {{ id 'com.android.application' }}
                android {{
                    namespace '{PACKAGE}'
                    compileSdk 34
                    defaultConfig {{
                        applicationId '{PACKAGE}'
                        minSdk 26
                        targetSdk 34
                        versionCode 1
                        versionName '1.0'
                        externalNativeBuild {{ cmake {{ cppFlags '-DPAGENT_ANDROID=1' }} }}
                        ndk {{ abiFilters 'arm64-v8a' }}
                    }}
                    externalNativeBuild {{ cmake {{ path 'src/main/cpp/CMakeLists.txt' }} }}
                    buildTypes {{ debug {{ debuggable true }} }}
                }}
                """
            ),
            encoding="utf-8",
        )
        (proj / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
            textwrap.dedent(
                f"""\
                <?xml version="1.0" encoding="utf-8"?>
                <manifest xmlns:android="http://schemas.android.com/apk/res/android">
                    <uses-feature android:glEsVersion="0x00030001" android:required="true"/>
                    <uses-permission android:name="android.permission.INTERNET"/>
                    <application android:label="{APP_LABEL}" android:hasCode="true">
                        <activity android:name=".MainActivity" android:exported="true"
                                  android:screenOrientation="landscape">
                            <intent-filter>
                                <action android:name="android.intent.action.MAIN"/>
                                <category android:name="android.intent.category.LAUNCHER"/>
                            </intent-filter>
                        </activity>
                    </application>
                </manifest>
                """
            ),
            encoding="utf-8",
        )
        (proj / "app" / "src" / "main" / "cpp" / "CMakeLists.txt").write_text(
            textwrap.dedent(
                """\
                cmake_minimum_required(VERSION 3.22)
                project(portagent C CXX)
                add_compile_options(-O2 -Wall)
                add_library(portagent SHARED
                    src/win32_shim.c
                    src/gfx_shim.c
                    src/audio_shim.c
                    native_lib.cpp)
                target_include_directories(portagent PRIVATE include)
                find_library(log-lib log)
                find_library(android-lib android)
                target_link_libraries(portagent ${log-lib} ${android-lib})
                """
            ),
            encoding="utf-8",
        )
        (proj / "app" / "src" / "main" / "cpp" / "native_lib.cpp").write_text(
            textwrap.dedent(
                """\
                #include <jni.h>
                #include "pagent_gfx_shim.h"
                #include "pagent_audio_shim.h"
                #include "pagent_win32_shim.h"

                extern "C" JNIEXPORT jstring JNICALL
                Java_dev_portagent_port_MainActivity_nativeBackend(JNIEnv* env, jobject) {
                    return env->NewStringUTF(pagent_gfx_backend());
                }

                extern "C" JNIEXPORT jint JNICALL
                Java_dev_portagent_port_MainActivity_nativeInit(JNIEnv*, jobject, jint w, jint h) {
                    if (pagent_gfx_init("gles3", w, h) != 0) return -1;
                    if (pagent_audio_init(48000, 2) != 0) return -2;
                    return 0;
                }
                """
            ),
            encoding="utf-8",
        )
        (proj / "app" / "src" / "main" / "java" / "dev" / "portagent" / "port" / "MainActivity.kt").write_text(
            textwrap.dedent(
                f"""\
                package {PACKAGE}

                import android.app.Activity
                import android.os.Bundle
                import android.util.Log

                class MainActivity : Activity() {{
                    external fun nativeInit(w: Int, h: Int): Int
                    external fun nativeBackend(): String

                    override fun onCreate(savedInstanceState: Bundle?) {{
                        super.onCreate(savedInstanceState)
                        val rc = nativeInit(1280, 720)
                        Log.i("PortAgent", "nativeInit=$rc backend=${{nativeBackend()}}")
                    }}

                    companion object {{
                        init {{ System.loadLibrary("portagent") }}
                    }}
                }}
                """
            ),
            encoding="utf-8",
        )
        return proj

    # -- native compilation ----------------------------------------------
    def compile_native(self, translated: dict, abi: str = "host") -> tuple[list[str], list[str], list[str]]:
        """Compile generated shims. Returns (objects, logs, commands)."""
        src = Path(translated["out_dir"])
        build = self.work_dir / "native_build" / abi
        build.mkdir(parents=True, exist_ok=True)
        cc = self._pick_compiler(abi)
        objects, logs, commands = [], [], []
        if cc is None:
            logs.append(f"no compiler available for abi={abi}")
            return objects, logs, commands
        for c in ("src/win32_shim.c", "src/gfx_shim.c", "src/audio_shim.c"):
            obj = build / (Path(c).stem + ".o")
            cmd = [cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-I", str(src / "include"), "-c",
                   str(src / c), "-o", str(obj)]
            if abi == "arm64-v8a":
                cmd = [cc, "-target", "aarch64-linux-android26", "-std=c11", "-O2",
                       "-I", str(src / "include"), "-c", str(src / c), "-o", str(obj)]
            rc, out = _run(cmd)
            commands.append(" ".join(cmd))
            if out.strip():
                logs.append(out.strip())
            if rc == 0 and obj.exists():
                objects.append(str(obj))
            else:
                logs.append(f"compile failed ({rc}) for {c}")
        return objects, logs, commands

    def _pick_compiler(self, abi: str) -> str | None:
        if abi == "arm64-v8a":
            ndk = os.environ.get("ANDROID_NDK_HOME") or os.environ.get("ANDROID_NDK_ROOT")
            if ndk:
                for cand in Path(ndk).glob("toolchains/llvm/prebuilt/*/bin/clang"):
                    return str(cand)
            return None
        for cand in ("clang", "gcc", "cc"):
            if shutil.which(cand):
                return cand
        return None

    # -- APK --------------------------------------------------------------
    def build_apk(self, proj: Path) -> tuple[str | None, list[str], list[str]]:
        logs, commands = [], []
        gradle = shutil.which("gradle")
        wrapper = proj / "gradlew"
        if gradle is None and not wrapper.exists():
            logs.append("gradle not found and no wrapper present; run scripts/bootstrap_android.sh")
            return None, logs, commands
        cmd = [str(wrapper), "assembleDebug", "--no-daemon"] if wrapper.exists() else [gradle, "assembleDebug", "--no-daemon"]
        commands.append(" ".join(cmd))
        rc, out = _run(cmd, cwd=proj, timeout=1800)
        logs.append(out[-4000:])
        if rc != 0:
            logs.append(f"gradle failed with rc={rc}")
            return None, logs, commands
        apks = sorted((proj / "app" / "build" / "outputs" / "apk").rglob("*.apk"))
        return (str(apks[0]) if apks else None), logs, commands

    # -- orchestration ----------------------------------------------------
    def build(self, profile: GameProfile, strategy: PortStrategy, translated: dict) -> BuildResult:
        res = BuildResult()
        proj = self.scaffold(profile, strategy, translated)
        res.project_dir = str(proj)

        objects, logs, commands = self.compile_native(translated, abi="host")
        res.objects += objects
        res.logs += logs
        res.commands += commands
        if objects:
            res.abis.append("host")

        ndk_objects, ndk_logs, ndk_cmds = self.compile_native(translated, abi="arm64-v8a")
        res.objects += ndk_objects
        res.logs += ndk_logs
        res.commands += ndk_cmds
        if ndk_objects:
            res.abis.append("arm64-v8a")

        apk, apk_logs, apk_cmds = self.build_apk(proj)
        res.logs += apk_logs
        res.commands += apk_cmds
        res.apk = apk

        if apk:
            res.status = "ok"
        elif objects:
            res.status = "partial"
        else:
            res.status = "fail"
        return res
