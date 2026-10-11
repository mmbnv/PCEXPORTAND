# PortAgent

Iterative PC-game → Android porter. PortAgent takes an installed game folder or
installer and drives the loop

```
ANALYZE → CHOOSE → EXECUTE → VERIFY → LEARN   (repeat)
```

until a build is **verified equivalent** to the original, or the game is
**formally declared impossible** for its class.

Every stage is a real component: binaries are parsed with pure-Python PE/ELF
readers, API translation emits compilable C shims, the builder scaffolds a
genuine Gradle/NDK project, and the verifier compiles and runs a scene harness
(`menu → gameplay → save`) and compares trace hashes against an oracle.

## Quick start

```bash
# reconnaissance only
python -m portagent analyze /path/to/game

# full loop (supply an oracle trace to enable equivalence proof)
python -m portagent run /path/to/game --work-dir ./run --oracle oracle.json

# inspect learned experience
python -m portagent memory --memory run/memory.sqlite
```

No third-party Python packages are required (stdlib only). Building an actual
APK additionally needs the Android SDK/NDK; install it with:

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
D3D11 title, RPG Maker MV, Godot, an already-ARM64 ELF, and a kernel-anti-cheat
title that must be declared impossible.

## What is implemented vs. scaffolded

| Area | Status |
|---|---|
| PE/ELF parsing (arch, imports, exports, sections) | **implemented**, pure Python |
| Engine / graphics / audio / DRM detection | **implemented** |
| Impossibility detection (kernel anti-cheat, .sys, VMProtect) | **implemented** |
| Memory (attempts, patch vectors, heuristic rules) | **implemented**, SQLite |
| Strategy planner with learned weighting | **implemented** |
| Translator → compilable Win32/gfx/audio C shims + mapping | **implemented** |
| Builder → Gradle/NDK project + host compile | **implemented** |
| Builder → signed APK | **scaffolded**; needs Android SDK (bootstrap script) |
| Verifier → determinism + scene equivalence | **implemented** |
| Verifier → on-device FPS/memory/temperature | **not implemented** (no device in CI) |
| Decompiler (Ghidra/dnSpy/jadx/Il2CppDumper) | **orchestrated** via bootstrap; not yet wired into automatic code reconstruction |

## Honest limitations

PortAgent is a working *pipeline* with real analysis, translation, build and
verification, not a magic "any game → APK" button. In particular:

- **Equivalence is only as good as the oracle.** Without a reference trace the
  verifier returns `UNKNOWN`, never a false `PASS`.
- **Full automatic reconstruction** of arbitrary game logic (the Decompiler →
  Translator → rebuild path) is the long pole. The current Translator generates
  the API-compatibility layer; wiring Ghidra/Il2CppDumper output into automatic
  source reconstruction is the next milestone.
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
