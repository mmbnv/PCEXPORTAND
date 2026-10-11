"""Translator (mission section 4, ``Translator``): the porting core.

Given a profile it emits, into an output directory:
  * ``mapping.json``     - the WinAPI/DirectX/XAudio -> Android mapping table,
  * ``include/pagent_win32_shim.h`` + ``src/win32_shim.c``   - Win32 -> POSIX,
  * ``include/pagent_gfx_shim.h``   + ``src/gfx_shim.c``     - D3D/GL -> Vulkan/GLES,
  * ``include/pagent_audio_shim.h`` + ``src/audio_shim.c``   - audio -> AAudio/OpenSL ES.

The generated C is self-contained and compiles with a plain C11 compiler on the
host (used by the Verifier as a translation smoke test); under the NDK it is
compiled against the real Android headers via the ``PAGENT_ANDROID`` define.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import ApiKind, GameProfile, PortStrategy

# Win32 -> POSIX/Android C function mapping.
WIN32_TO_POSIX: dict[str, str] = {
    "CreateFileA": "pagent_open",
    "CreateFileW": "pagent_open_w",
    "ReadFile": "pagent_read",
    "WriteFile": "pagent_write",
    "CloseHandle": "pagent_close",
    "Sleep": "pagent_sleep_ms",
    "GetTickCount": "pagent_tick_ms",
    "GetTickCount64": "pagent_tick_ms",
    "QueryPerformanceCounter": "pagent_qpc",
    "CreateThread": "pthread_create",
    "WaitForSingleObject": "pagent_wait",
    "GetSystemTime": "pagent_gettime",
    "GetLocalTime": "pagent_gettime",
    "GetModuleFileNameA": "pagent_module_path",
    "VirtualAlloc": "pagent_virtual_alloc",
    "VirtualFree": "pagent_virtual_free",
    "GetLastError": "pagent_last_error",
    "SetLastError": "pagent_set_last_error",
}

GFX_MAPPING: dict[ApiKind, dict[str, str]] = {
    ApiKind.D3D9: {"target": "vulkan", "layer": "DXVK (D3D9 -> Vulkan)", "api": "VkInstance/VkDevice"},
    ApiKind.D3D11: {"target": "vulkan", "layer": "DXVK (D3D11 -> Vulkan)", "api": "VkInstance/VkDevice"},
    ApiKind.D3D12: {"target": "vulkan", "layer": "VKD3D-Proton (D3D12 -> Vulkan)", "api": "VkInstance/VkDevice"},
    ApiKind.OPENGL: {"target": "gles", "layer": "native (OpenGL ES 3.1)", "api": "EGL/GLESv3"},
    ApiKind.VULKAN: {"target": "vulkan", "layer": "native (Vulkan)", "api": "VkInstance/VkDevice"},
    ApiKind.SDL2: {"target": "sdl2", "layer": "bundled SDL2 for Android", "api": "SDL2"},
}

AUDIO_MAPPING: dict[ApiKind, dict[str, str]] = {
    ApiKind.XAUDIO: {"target": "aaudio", "layer": "AAudio (API 26+)", "api": "AAudioStream"},
    ApiKind.DIRECTSOUND: {"target": "opensl_es", "layer": "OpenSL ES", "api": "SLObjectItf"},
    ApiKind.WINDOWS_MME: {"target": "aaudio", "layer": "AAudio", "api": "AAudioStream"},
    ApiKind.SDL2: {"target": "sdl2_audio", "layer": "SDL2 audio backend", "api": "SDL_AudioDeviceID"},
}


class Translator:
    def __init__(self, out_dir: str | Path):
        self.out_dir = Path(out_dir)

    def translate(self, profile: GameProfile, strategy: PortStrategy) -> dict:
        out = self.out_dir
        (out / "include").mkdir(parents=True, exist_ok=True)
        (out / "src").mkdir(parents=True, exist_ok=True)

        mapping = self._build_mapping(profile)
        (out / "mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        self._write_win32_shim(out)
        self._write_gfx_shim(out, profile)
        self._write_audio_shim(out, profile)

        return {
            "out_dir": str(out),
            "mapping": mapping,
            "files": [
                "mapping.json",
                "include/pagent_win32_shim.h",
                "src/win32_shim.c",
                "include/pagent_gfx_shim.h",
                "src/gfx_shim.c",
                "include/pagent_audio_shim.h",
                "src/audio_shim.c",
            ],
        }

    # -- mapping table ----------------------------------------------------
    def _build_mapping(self, profile: GameProfile) -> dict:
        gfx = profile.graphics_api
        audio = profile.audio_api
        return {
            "engine": profile.engine.value,
            "arch": profile.arch,
            "win32": WIN32_TO_POSIX,
            "graphics": (
                GFX_MAPPING.get(gfx, {"target": "gles", "layer": "fallback GLES", "api": "EGL/GLESv3"})
                if gfx
                else {"target": "gles", "layer": "no graphics API detected; fallback GLES", "api": "EGL/GLESv3"}
            ),
            "audio": (
                AUDIO_MAPPING.get(audio, {"target": "aaudio", "layer": "fallback AAudio", "api": "AAudioStream"})
                if audio
                else {"target": "aaudio", "layer": "no audio API detected; fallback AAudio", "api": "AAudioStream"}
            ),
            "input": {
                "source": "touch/gamepad",
                "layer": "pagent_input (touch -> mouse/keyboard mapping)",
            },
            "filesystem": {"source": "Win32 paths", "target": "app-private + scoped storage"},
            "registry": {"source": "Win32 registry", "target": "INI/JSON shim in app-private dir"},
        }

    # -- generated sources ------------------------------------------------
    def _write_win32_shim(self, out: Path) -> None:
        header = """/* Auto-generated by PortAgent Translator. Do not edit by hand. */
#ifndef PAGENT_WIN32_SHIM_H
#define PAGENT_WIN32_SHIM_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int   pagent_open(const char *path, int flags);
long  pagent_read(int fd, void *buf, unsigned long n);
long  pagent_write(int fd, const void *buf, unsigned long n);
int   pagent_close(int fd);
void  pagent_sleep_ms(unsigned ms);
uint64_t pagent_tick_ms(void);
uint64_t pagent_qpc(void);
void *pagent_virtual_alloc(size_t n);
void  pagent_virtual_free(void *p);
int   pagent_last_error(void);
void  pagent_set_last_error(int e);
#ifdef __cplusplus
}
#endif
#endif /* PAGENT_WIN32_SHIM_H */
"""
        source = """/* Auto-generated by PortAgent Translator: Win32 -> POSIX/Android. */
#define _POSIX_C_SOURCE 200809L
#include "pagent_win32_shim.h"
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static int g_last_error = 0;

int pagent_open(const char *path, int flags) {
    int fd = open(path, flags);
    if (fd < 0) g_last_error = errno;
    return fd;
}
long pagent_read(int fd, void *buf, unsigned long n) { return (long)read(fd, buf, n); }
long pagent_write(int fd, const void *buf, unsigned long n) { return (long)write(fd, buf, n); }
int pagent_close(int fd) { return close(fd); }
void pagent_sleep_ms(unsigned ms) {
    struct timespec ts; ts.tv_sec = ms / 1000u; ts.tv_nsec = (long)(ms % 1000u) * 1000000L;
    nanosleep(&ts, 0);
}
uint64_t pagent_tick_ms(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)(ts.tv_nsec / 1000000L);
}
uint64_t pagent_qpc(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}
void *pagent_virtual_alloc(size_t n) { return malloc(n); }
void pagent_virtual_free(void *p) { free(p); }
int pagent_last_error(void) { return g_last_error; }
void pagent_set_last_error(int e) { g_last_error = e; }
"""
        (out / "include" / "pagent_win32_shim.h").write_text(header, encoding="utf-8")
        (out / "src" / "win32_shim.c").write_text(source, encoding="utf-8")

    def _write_gfx_shim(self, out: Path, profile: GameProfile) -> None:
        gfx = profile.graphics_api
        target = GFX_MAPPING.get(gfx, {}).get("target", "gles") if gfx else "gles"
        header = """/* Auto-generated by PortAgent Translator. Do not edit by hand. */
#ifndef PAGENT_GFX_SHIM_H
#define PAGENT_GFX_SHIM_H
#ifdef __cplusplus
extern "C" {
#endif
/* Returns 0 on success. backend is "vulkan" or "gles". */
int pagent_gfx_init(const char *backend, int width, int height);
void pagent_gfx_present(void);
void pagent_gfx_shutdown(void);
const char *pagent_gfx_backend(void);
#ifdef __cplusplus
}
#endif
#endif /* PAGENT_GFX_SHIM_H */
"""
        source = f"""/* Auto-generated by PortAgent Translator: graphics -> {target}. */
#include "pagent_gfx_shim.h"
#include <string.h>

static char g_backend[16] = "{target}";
static int g_w = 0, g_h = 0, g_ready = 0;

int pagent_gfx_init(const char *backend, int width, int height) {{
    if (backend && backend[0]) {{
        strncpy(g_backend, backend, sizeof(g_backend) - 1);
        g_backend[sizeof(g_backend) - 1] = 0;
    }}
    g_w = width; g_h = height;
#if defined(PAGENT_ANDROID)
    /* Real path: create VkInstance/VkDevice or EGL surface here. */
#endif
    g_ready = (width > 0 && height > 0);
    return g_ready ? 0 : -1;
}}
void pagent_gfx_present(void) {{ /* vkQueuePresentKHR / eglSwapBuffers */ }}
void pagent_gfx_shutdown(void) {{ g_ready = 0; }}
const char *pagent_gfx_backend(void) {{ return g_backend; }}
"""
        (out / "include" / "pagent_gfx_shim.h").write_text(header, encoding="utf-8")
        (out / "src" / "gfx_shim.c").write_text(source, encoding="utf-8")

    def _write_audio_shim(self, out: Path, profile: GameProfile) -> None:
        audio = profile.audio_api
        target = AUDIO_MAPPING.get(audio, {}).get("target", "aaudio") if audio else "aaudio"
        header = """/* Auto-generated by PortAgent Translator. Do not edit by hand. */
#ifndef PAGENT_AUDIO_SHIM_H
#define PAGENT_AUDIO_SHIM_H
#ifdef __cplusplus
extern "C" {
#endif
int pagent_audio_init(int sample_rate, int channels);
int pagent_audio_write(const void *frames, int frame_count);
void pagent_audio_shutdown(void);
const char *pagent_audio_backend(void);
#ifdef __cplusplus
}
#endif
#endif /* PAGENT_AUDIO_SHIM_H */
"""
        source = f"""/* Auto-generated by PortAgent Translator: audio -> {target}. */
#include "pagent_audio_shim.h"
#include <stddef.h>

static const char *g_backend = "{target}";
static int g_rate = 0, g_channels = 0, g_ready = 0;

int pagent_audio_init(int sample_rate, int channels) {{
    g_rate = sample_rate; g_channels = channels;
#if defined(PAGENT_ANDROID)
    /* Real path: AAudioStreamBuilder_openStream or SLES engine. */
#endif
    g_ready = (sample_rate > 0 && channels > 0);
    return g_ready ? 0 : -1;
}}
int pagent_audio_write(const void *frames, int frame_count) {{
    if (!g_ready || !frames) return -1;
    (void)frame_count;
    return 0;
}}
void pagent_audio_shutdown(void) {{ g_ready = 0; }}
const char *pagent_audio_backend(void) {{ return g_backend; }}
"""
        (out / "include" / "pagent_audio_shim.h").write_text(header, encoding="utf-8")
        (out / "src" / "audio_shim.c").write_text(source, encoding="utf-8")
