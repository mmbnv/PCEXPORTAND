# PortAgent

Iterative PC-game → Android porter. PortAgent takes an installed game folder or
installer and drives the loop

```
ANALYZE → CHOOSE → DECOMPILE → EXECUTE → VERIFY → LEARN   (repeat)
```

until a build is **verified equivalent** to the original, or the game is
**formally declared impossible** for its class.

Every stage is a real component: binaries are parsed with pure-Python PE/ELF
readers, code is recovered with real tools (monodis/objdump/jadx/Il2CppDumper),
API translation emits compilable C shims, an embedded runtime layer dispatches to
the right backend, and the **packager produces a real, installable, signed APK**
using the Android build-tools directly (aapt2, javac, d8, zipalign, apksigner) —
no Gradle and no network. The verifier compiles and runs a scene harness
(`menu → gameplay → save`) and compares trace hashes against an oracle.

## Quick start

### Friendly app (no terminal commands to memorise)

```bash
python run.py            # interactive menu: pick a folder, Analyze, Build APK, get the report
python run.py --gui      # the same thing as a real desktop window
python -m portagent gui  # open the desktop GUI directly
```

The menu and GUI both let you: browse to a game folder or installer, run
**Analyze**, **Build APK** or the **full loop**, and then copy/save a stats
report to send back. The GUI needs Tkinter (on Debian/Ubuntu:
`apt-get install python3-tk`); the menu works without it.

### Command line

```bash
# one command: game folder -> installable, signed APK
python -m portagent port "C:\Games\MyGame" -o MyGame.apk

# every strategy class at once: build all built-in fixtures -> signed APKs
python -m portagent portall --work-dir ./all

# reconnaissance only
python -m portagent analyze /path/to/game

# full loop (supply an oracle trace to enable equivalence proof)
python -m portagent run /path/to/game --work-dir ./run --oracle oracle.json

# inspect learned experience
python -m portagent memory --memory run/memory.sqlite
```

No third-party Python packages are required (stdlib only). `port` and real APK
building additionally need the Android SDK/NDK; install it with:

```bash
scripts/bootstrap_android.sh          # JDK + Android SDK + NDK + Gradle
scripts/bootstrap_android.sh --full   # + Ghidra, jadx, apktool, Il2CppDumper
```

## Pilot

```bash
python scripts/pilot.py --out pilot
```

Runs the loop over a set of synthetic-but-real game folders (compiled with
mingw-w64 / aarch64-linux-gnu-gcc) covering Unity IL2CPP, Unity Mono, a 32-bit
D3D11 title, RPG Maker MV, Godot, Godot .NET (C#) with Steamworks, an
already-ARM64 ELF, and a kernel-anti-cheat title that must be declared
impossible.

## What `port` actually produces

A real, installable, signed APK containing:

```
AndroidManifest.xml                 landscape launcher Activity
classes.dex                         the embedded runtime layer (PortActivity)
lib/arm64-v8a/libportagent.so       compiled compatibility shims (NDK clang)
assets/game/**                      the full game payload, unpacked on first run
assets/game/portagent_runtime/      runtime class + env.json (+ bundled libs)
```

The Activity unpacks `assets/game/**` into app-private storage, then dispatches
on the **runtime class** chosen for the title:

| Runtime class | Used for | What it does |
|---|---|---|
| `engine_native` | Unity / Unreal / Godot / GameMaker / RPG Maker | runs the engine's own ARM64 runtime on the data pack |
| `mono_managed` | .NET / Mono titles | boots Mono and loads the recovered managed assemblies |
| `dbt_box64` | native x86/x64 titles | runs the original exe under Box64 dynamic binary translation |
| `wine_dxvk` | native Windows + D3D titles | Box64 + Wine + DXVK/VKD3D translation stack |

The runtime class and its environment are written to
`assets/game/portagent_runtime/env.json` at build time. The heavy ARM64 runtime
payloads (Box64, Wine, DXVK) are **not** vendored in this repo; `scripts/fetch_runtimes.sh`
pulls them into `~/.portagent-tools/runtime/<class>/`, and the packager bundles
whatever is installed. When a runtime is absent the app says so by name — it
never pretends to have run the game.

`apksigner verify` passes (v2+v3 schemes) and `zipalign -c` passes.

**Honest boundary:** this is a *valid, launchable Android app that carries the
game, the compatibility layer, and the runtime dispatch*. It does **not** yet
recompile arbitrary game logic into Kotlin/C++ — that requires the
Decompiler → Translator → rebuild path (Ghidra / Il2CppDumper / ILSpy output
wired into source reconstruction), which is the next milestone. For engines with
a first-party Android exporter the payload + engine runtime is the correct
container; for native x86/x64 titles the runtime layer mounts Box64/Wine/DXVK.

## What is implemented vs. scaffolded

| Area | Status |
|---|---|
| PE/ELF parsing (arch, imports, exports, sections) | **implemented**, pure Python |
| Engine / graphics / audio / DRM detection | **implemented** |
| Impossibility detection (kernel anti-cheat, .sys, VMProtect) | **implemented** |
| Memory (attempts, patch vectors, heuristic rules) | **implemented**, SQLite |
| Strategy planner with learned weighting | **implemented** |
| Decompiler (monodis IL / objdump asm / jadx dex / Il2CppDumper) | **implemented**, real tools, in the loop |
| Translator → compilable Win32/gfx/audio C shims + mapping | **implemented** |
| Builder → Gradle/NDK project + host compile | **implemented** |
| Optimizer → ABI/backend/heap/DBT plan written into the APK | **implemented** |
| Runtime layer → dispatch + payload unpack + runtime class config | **implemented** |
| **Packager → real installable signed APK (aapt2/d8/zipalign/apksigner)** | **implemented**, verified with `apksigner verify` |
| Verifier → determinism + scene equivalence | **implemented** |
| Verifier → on-device FPS/memory/temperature | **not implemented** (no device in CI) |
| Runtime binaries (Box64/Wine/DXVK ARM64 builds) | **not vendored**; fetched via `scripts/fetch_runtimes.sh` |

## Honest limitations

PortAgent is a working *pipeline* with real analysis, decompilation, translation,
runtime dispatch, build and verification, not a magic "any game → APK" button. In
particular:

- **Equivalence is only as good as the oracle.** Without a reference trace the
  verifier returns `UNKNOWN`, never a false `PASS`.
- **Full automatic reconstruction** of arbitrary game logic (Decompiler →
  Translator → rebuild) is the long pole. The Decompiler now really recovers IL
  and disassembly and the runtime layer really dispatches, but compiling recovered
  code back into a native ARM64 game is the next milestone.
- **Runtime payloads are external.** Box64/Wine/DXVK ARM64 builds are large and
  licence-encumbered; they are fetched separately, and the app reports which one
  is missing.
- **No KVM / no device** in this environment, so performance thresholds and
  on-device runs are not exercised here.
- The "converges with probability 1" claim from the brief is not a real theorem
  (no well-defined probability space over "all games"). The loop terminates on
  success, on a hard blocker, or when the finite strategy space is exhausted.
  Rice's theorem and the halting problem are acknowledged: equivalence for all
  games is undecidable, which is exactly why the verifier demands a concrete
  oracle and says `UNKNOWN` otherwise.

See `docs/ARCHITECTURE.md` for the design, `docs/USAGE.md` for a step-by-step
usage guide (Russian), and `docs/TOOLCHAIN.md` for setup.
