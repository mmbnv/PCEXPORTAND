"""Build synthetic *real* game folders for tests and the pilot.

Every binary here is compiled for real (mingw-w64 for PE, aarch64-linux-gnu-gcc
for ELF) so the Analyzer's PE/ELF parsers are exercised against genuine files
rather than fakes.  Layouts mimic real games: Unity IL2CPP, Unity Mono, a
native D3D11/x86 title, an RPG Maker MV folder, and a kernel-anti-cheat title
that must be declared impossible.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

MINGW64 = shutil.which("x86_64-w64-mingw32-gcc")
MINGW32 = shutil.which("i686-w64-mingw32-gcc")
AARCH64 = shutil.which("aarch64-linux-gnu-gcc")
MCS = shutil.which("mcs")


@dataclass
class Fixture:
    name: str
    path: Path
    engine: str
    expect_feasible: bool
    notes: str = ""


def _cc_available() -> bool:
    return bool(MINGW64 and AARCH64)


def _write_exe(cc: str, out: Path, dlls: list[str], source: str) -> bool:
    """Compile ``source`` into a PE whose import table references ``dlls``.

    Each requested DLL is referenced through a real API call so the linker keeps
    it in the PE import table.  Proper SDK headers are used instead of ad-hoc
    prototypes to stay compatible with the mingw-w64 headers.
    """
    specs: dict[str, tuple[list[str], list[str], list[str]]] = {
        # dll -> (extra includes, local declarations, statements)
        "d3d11": (["d3d11.h"], ["ID3D11Device *d = NULL; ID3D11DeviceContext *c = NULL;"],
                  ["D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, NULL, 0, NULL, 0,"
                   " D3D11_SDK_VERSION, &d, NULL, &c);"]),
        "d3d12": (["d3d12.h"], [], ["D3D12CreateDevice(NULL, (D3D_FEATURE_LEVEL)0, NULL, NULL);"]),
        "d3d9": (["d3d9.h"], [], ["Direct3DCreate9(D3D_SDK_VERSION);"]),
        "dxgi": (["dxgi.h"], [], ["CreateDXGIFactory(NULL, NULL);"]),
        "opengl32": (["windows.h"], ["PROC __attribute__((unused)) p_ = wglGetProcAddress(\"glClear\"); (void)p_;"],
                     []),
        "xaudio2_9": (["xaudio2.h"], ["IXAudio2 *xa = NULL;"],
                      ["XAudio2Create(&xa, 0, XAUDIO2_DEFAULT_PROCESSOR);"]),
        "xaudio2_8": (["xaudio2.h"], ["IXAudio2 *xa = NULL;"],
                      ["XAudio2Create(&xa, 0, XAUDIO2_DEFAULT_PROCESSOR);"]),
        "dsound": (["dsound.h"], ["LPDIRECTSOUND ds = NULL;"],
                   ["DirectSoundCreate(NULL, &ds, NULL);"]),
        "winmm": (["mmsystem.h"], [], ["timeGetTime();"]),
        "user32": ([], [], ["MessageBoxA(NULL, NULL, NULL, 0);"]),
        "kernel32": ([], [], ["GetTickCount();"]),
        "ws2_32": (["winsock2.h"], ["WSADATA w;"], ["WSAStartup(MAKEWORD(2,2), &w);"]),
        "ole32": (["objbase.h"], [], ["CoInitialize(NULL);"]),
        "shell32": (["shlobj.h"], [], ["SHGetFolderPathA(NULL, 0, NULL, 0, NULL);"]),
    }
    includes = ["stdint.h"]
    locals_, stmts = [], []
    for d in dlls:
        inc, loc, st = specs.get(d, ([], [], []))
        for i in inc:
            if i not in includes:
                includes.append(i)
        locals_ += loc
        stmts += st
    # winsock2.h must precede windows.h (pulled in by the D3D headers).
    if "winsock2.h" in includes:
        includes.remove("winsock2.h")
        includes.insert(1, "winsock2.h")
    inc_lines = "\n".join(f"#include <{i}>" for i in includes)
    if "main(" in source:
        full = (
            inc_lines + "\n"
            + "static void __attribute__((used)) __pagent_import_refs(void) {\n"
            + "\n".join(locals_) + "\n" + "\n".join(stmts) + "\n}\n"
            + source
        )
    else:
        full = f"""{inc_lines}
int main(void) {{
{chr(10).join(locals_)}
{chr(10).join(stmts)}
{source}
    return 0;
}}
"""
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "main.c"
        c.write_text(full, encoding="utf-8")
        cmd = [cc, "-O0", str(c), "-o", str(out)] + [f"-l{d}" for d in dlls]
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode == 0 and out.exists()


def _write_dll(cc: str, out: Path, source: str) -> bool:
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "lib.c"
        c.write_text(source, encoding="utf-8")
        cmd = [cc, "-shared", "-O2", str(c), "-o", str(out)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode == 0 and out.exists()


def _touch(p: Path, data: bytes = b"\x00" * 64) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def _write_assembly(out: Path, source: str) -> bool:
    """Compile real .NET IL with mcs when available; else a PE stub."""
    if not MCS:
        _touch(out, b"\x4d\x5a" + b"\x00" * 2048)
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        c = Path(td) / "asm.cs"
        c.write_text(source, encoding="utf-8")
        p = subprocess.run(
            [MCS, "-target:library", "-out:" + str(out), str(c)],
            capture_output=True, text=True,
        )
        ok = p.returncode == 0 and out.exists()
        if not ok:
            _touch(out, b"\x4d\x5a" + b"\x00" * 2048)
        return ok


_ASSEMBLY_CSHARP = """using System;
namespace Game {
  public class Player {
    public int Health; public string Name;
    public Player(string n) { Name = n; Health = 100; }
    public int Damage(int a) { Health -= a; return Health; }
    public bool Alive() { return Health > 0; }
  }
  public class World {
    public void Update() { var p = new Player("hero"); p.Damage(5); }
  }
}
"""


def _unity_il2cpp(root: Path) -> None:
    _write_dll(
        MINGW64,
        root / "GameName_Data" / "Plugins" / "x86_64" / "GameAssembly.dll",
        "extern int il2cpp_init(void); int il2cpp_init(void){return 0;}\n"
        "int il2cpp_runtime_invoke(void){return 0;}\n",
    )
    _touch(root / "GameName_Data" / "il2cpp_data" / "Metadata" / "global-metadata.dat",
           b"\xaf\x1b\xb1\xfa" + b"\x00" * 4096)
    _touch(root / "GameName_Data" / "globalgamemanagers", b"\x00" * 512)
    _write_exe(
        MINGW64,
        root / "GameName.exe",
        ["kernel32", "user32", "d3d11", "xaudio2_9"],
        "" ,
    )
    _write_dll(MINGW64, root / "UnityPlayer.dll", "int unity_player(void){return 1;}\n")
    _write_dll(MINGW64, root / "d3d11.dll", "int d3d(void){return 0;}\n")


def _unity_mono(root: Path) -> None:
    _write_assembly(root / "MonoGame_Data" / "Managed" / "Assembly-CSharp.dll", _ASSEMBLY_CSHARP)
    _touch(root / "MonoGame_Data" / "Mono" / "mono-2.0-sgen.dll",
           b"\x4d\x5a" + b"\x00" * 1024)
    _touch(root / "MonoGame_Data" / "globalgamemanagers", b"\x00" * 512)
    _write_exe(
        MINGW64,
        root / "MonoGame.exe",
        ["kernel32", "user32", "d3d11", "winmm"],
        "" ,
    )
    _write_dll(MINGW64, root / "UnityPlayer.dll", "int unity_player(void){return 1;}\n")


def _native_d3d11_x86(root: Path) -> None:
    _write_exe(
        MINGW32,
        root / "IndieGame.exe",
        ["kernel32", "user32", "d3d11", "dxgi", "xaudio2_9", "ws2_32"],
        "" ,
    )
    _write_dll(MINGW32, root / "steam_api.dll", "int SteamAPI_Init(void){return 0;}\n")
    _touch(root / "data" / "assets.pak", b"\x00" * 1024)


def _rpgmaker_mv(root: Path) -> None:
    _touch(root / "www" / "js" / "plugins" / "PluginManager.js", b"// rpg maker mv\n")
    _touch(root / "www" / "data" / "System.json", b'{"gameTitle":"Test"}\n')
    _write_exe(MINGW32, root / "nw.exe", ["kernel32", "user32"], "int main(void){return 0;}\n")


def _anticheat_title(root: Path) -> None:
    _write_exe(
        MINGW64,
        root / "CompetitiveGame.exe",
        ["kernel32", "user32", "d3d11"],
        "" ,
    )
    _touch(root / "EasyAntiCheat" / "EasyAntiCheat.sys", b"\x4d\x5a" + b"\x00" * 512)
    _touch(root / "EasyAntiCheat" / "EasyAntiCheat_x64.dll", b"\x4d\x5a" + b"\x00" * 512)


def _godot(root: Path) -> None:
    _write_exe(MINGW64, root / "GodotGame.exe", ["kernel32", "user32", "opengl32"],
               "int main(void){return 0;}\n")
    _touch(root / "godot" / "project.binary", b"\x00" * 256)


def _godot_dotnet(root: Path) -> None:
    """Godot .NET (C#) title with a Steamworks wrapper and FMOD, like Slay the Spire 2."""
    _write_exe(MINGW64, root / "GodotGame.exe", ["kernel32", "user32", "opengl32"],
               "int main(void){return 0;}\n")
    _touch(root / "Game.pck", b"GDPC" + b"\x00" * 512)
    _touch(root / "GodotSharp.dll", b"\x4d\x5a" + b"\x00" * 1024)
    # Managed .NET assemblies (real IL when mcs is available).
    for name in ("sts2.dll", "System.Private.CoreLib.dll", "Steamworks.NET.dll"):
        _write_assembly(root / "data_sts2_windows_x86_64" / name, _ASSEMBLY_CSHARP)
    _write_dll(MINGW64, root / "fmod.dll", "int FMOD_System_Create(void){return 0;}\n")
    _write_dll(MINGW64, root / "steam_api64.dll", "int SteamAPI_Init(void){return 0;}\n")


def _elf_arm64(root: Path) -> None:
    src = Path(root) / "lib" / "libengine.c"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("int engine_init(void){return 0;}\n", encoding="utf-8")
    subprocess.run(
        [AARCH64, "-shared", "-fPIC", "-O2", str(src), "-o", str(root / "lib" / "libengine.so")],
        capture_output=True, text=True,
    )


def build_all(dest: str | Path) -> list[Fixture]:
    """Create every fixture under ``dest`` and return their metadata."""
    if not _cc_available():
        raise RuntimeError("cross compilers required: mingw-w64 + gcc-aarch64-linux-gnu")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    fixtures: list[Fixture] = []

    def mk(name: str, fn, engine: str, feasible: bool, notes: str = "") -> None:
        root = dest / name
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        fn(root)
        fixtures.append(Fixture(name, root, engine, feasible, notes))

    mk("unity_il2cpp", _unity_il2cpp, "unity", True, "IL2CPP native Unity title")
    mk("unity_mono", _unity_mono, "unity", True, "Mono-managed Unity title")
    mk("native_d3d11_x86", _native_d3d11_x86, "custom_native", True, "32-bit D3D11 + Steam")
    mk("rpgmaker_mv", _rpgmaker_mv, "rpgmaker", True, "RPG Maker MV web runtime")
    mk("anticheat_title", _anticheat_title, "custom_native", False, "kernel anti-cheat -> impossible")
    mk("godot", _godot, "godot", True, "Godot GLES title")
    mk("godot_dotnet", _godot_dotnet, "godot", True, "Godot .NET (C#) + Steamworks + FMOD")
    mk("elf_arm64", _elf_arm64, "custom_native", True, "already-ARM64 ELF shared lib")
    return fixtures


if __name__ == "__main__":
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "fixtures"
    for f in build_all(out):
        print(f"{f.name:20s} engine={f.engine:14s} feasible={f.expect_feasible}  {f.notes}")
