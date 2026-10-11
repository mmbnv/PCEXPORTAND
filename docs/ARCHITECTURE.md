# Architecture

## Pipeline

```
[Input: installer / installed game folder]
        в”Ӯ
        в–ј
в”Ңв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”җ
в”Ӯ PortAgent orchestrator (policy)                               в”Ӯ
в”Ӯ   Analyzer   вҖ“ reconnaissance (PE/ELF, engine, APIs, DRM)      в”Ӯ
в”Ӯ   Planner    вҖ“ CHOOSE strategy, weighted by Memory             в”Ӯ
в”Ӯ   Decompiler вҖ“ recover code (monodis/objdump/jadx/Il2CppDumper)в”Ӯ
в”Ӯ   Translator вҖ“ WinAPI/DirectX/audio вҶ’ Android C shims          в”Ӯ
в”Ӯ   Builder    вҖ“ Gradle/NDK project + native compile             в”Ӯ
в”Ӯ   Optimizer  вҖ“ ABI/backend/heap/DBT plan                       в”Ӯ
в”Ӯ   Runtime    вҖ“ pick runtime class + env, bundle payload        в”Ӯ
в”Ӯ   Packager   вҖ“ real signed APK (aapt2/d8/zipalign/apksigner)   в”Ӯ
в”Ӯ   Verifier   вҖ“ determinism + scene equivalence vs oracle       в”Ӯ
в”Ӯ   Memory     вҖ“ SQLite: attempts, patch vectors, heuristic rulesв”Ӯ
в””в”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”Җв”ҳ
        в”Ӯ
        в–ј
[Output: APK + equivalence report]  or  [IMPOSSIBLE + evidence]
```

## Entry points

Three front ends drive the same pipeline through the shared, headless
`portagent/launcher.py` API вҖ” so the CLI, the menu and the GUI never diverge:

```
 run.py (menu)  в”Җв”җ
 portagent.gui  в”Җв”јв”Җв–ә  portagent.launcher  в”Җв–ә  Analyzer/Planner/Decompiler/
 portagent.cli  в”Җв”ҳ                            Translator/Optimizer/Runtime/
                                              Packager  (+ orchestrator loop)
```

- `launcher.analyze()` / `run_port()` / `run_loop()` вҖ” headless stages; each
  takes an optional `progress(stage, message)` callback.
- `launcher.environment()` вҖ” host/toolchain probe, embedded in every report.
- `launcher.build_report()` / `format_markdown()` вҖ” a copy-paste stats report
  (status, engine, strategy, runtime, APK, timings, environment) the user can
  send back for support.
- The GUI runs the long stages on a worker thread and marshals progress back to
  Tk with `after()`, so the window never freezes. Tkinter is optional; when it is
  missing the menu still works and `portagent gui` prints a clear hint.

## Loop

1. **ANALYZE** вҖ“ walk the folder, parse every PE/ELF (`portagent/parsers.py`),
   score engine signatures, map imported DLLs to runtime APIs, detect DRM /
   anti-cheat, and record hard blockers. Produces `GameProfile` (JSON).
2. **CHOOSE** вҖ“ `Planner` ranks candidate strategies from static engine/API
   rules plus learned weights from `Memory`. Hard blockers short-circuit to
   `UNSUPPORTED`.
3. **DECOMPILE** вҖ“ `Decompiler` runs the real recovery tools for the title's
   class: `monodis` вҶ’ IL for managed assemblies, `objdump` вҶ’ native disassembly,
   `jadx` вҶ’ Java from DEX, `Il2CppDumper` вҶ’ metadata for Unity IL2CPP. The result
   (`DecompileResult`) records which tools ran and what was recovered.
4. **EXECUTE** вҖ“ `Translator` writes a mapping table and three compilable C
   shims (`win32`, `gfx`, `audio`); `Builder` scaffolds a real Gradle/CMake
   Android project and host-compiles the shims; `Optimizer` decides ABI/backend/
   heap/DBT tuning; `RuntimePlanner` picks the runtime class and environment;
   `ApkPackager` builds a real, installable, signed APK (aapt2 вҶ’ javac вҶ’ d8 вҶ’
   zip вҶ’ zipalign вҶ’ apksigner), embedding the game under `assets/game/**`, the
   runtime config under `assets/game/portagent_runtime/`, and the shims as
   `lib/arm64-v8a/libportagent.so`.
5. **VERIFY** вҖ“ `Verifier` compiles the shims twice (determinism), builds and
   runs a C scene harness across `menu вҶ’ gameplay вҶ’ save`, hashes each trace,
   and compares to the oracle. Only an exact match yields `PASS`.
6. **LEARN** вҖ“ the outcome is written to `Memory` (attempt row + rule weight
   update + patch vector on success). The next iteration consults it.

Termination: `ACCEPT` (verified), `IMPOSSIBLE` (hard blocker), `exhausted`
(finite strategy space consumed), or `inconclusive` (iteration budget spent).

## Runtime layer

The packager's launcher Activity is an **embedded runtime layer** that dispatches
on a runtime class chosen from the profile and strategy:

| Class | Chosen when | Launch behaviour |
|---|---|---|
| `wine_dxvk` | any source-less x86/x64 title (incl. Unity/Unreal/Godot builds) | unpack the bundled stack, then `loader → box64 → wine → game.exe` with DXVK/VKD3D and Turnip |
| `dbt_box64` | native x86/x64 title without Windows APIs | run the original exe under Box64 |
| `engine_native` | only when the engine's sources/export are available | run the engine's ARM64 runtime on the data pack |
| `mono_managed` | .NET/Mono title | boot Mono, load recovered assemblies |

For a game **without source code** the runtime layer always selects
`wine_dxvk`; it is the only path that actually executes the prebuilt x86/x64
binary on ARM64. The generated `PortActivity`:

- starts Box64 **through the shipped aarch64 loader**
  (`rootfs/usr/lib/ld-linux-aarch64.so.1`) because Box64's own `PT_INTERP` points
  at Winlator's private package path;
- sets `WINELOADERNOEXEC=1` and `WINEDLLPATH` because the Wine loader stub execs a
  preloader from its build path, which does not exist in our relocated rootfs;
- stages the DXVK / VKD3D DLLs from `assets/runtime/{dxvk,vkd3d}` into the Wine
  prefix's `system32` / `syswow64` and sets `WINEDLLOVERRIDES=...=n,b` so Wine
  resolves D3D to them instead of its own builtins;
- rewrites the Turnip ICD (`freedreno_icd.aarch64.json`) to point at the unpacked
  `libvulkan_freedreno.so` rather than Winlator's private path.

The chain was validated offline with `qemu-aarch64`: `loader → box64 → wine`
reports `wine-10.10` and reaches Wine's PE initialisation.

`RuntimePlanner` writes the class, entry point, backend, translation and env vars
(`PAGENT_ENGINE`, `PAGENT_STRATEGY`, `BOX64_*`, `DXVK_*`) to
`assets/game/portagent_runtime/env.json`. `RuntimeBundler` copies the prebuilt
ARM64 stack from `~/.portagent-tools/runtime/wine_dxvk/` into the APK under
`assets/runtime/**` (box64 + wine rootfs + dxvk + vkd3d + turnip). That stack is
populated by `python -m portagent fetch-runtimes` (or `scripts/fetch_runtimes.sh`),
which downloads a **pinned Winlator 11.2.0** release and verifies the APK SHA-256
and the ELF architecture of every binary. When a payload is missing the app
reports the missing binary by name rather than silently failing.

## Step report contract

Every stage emits the JSON required by the brief:

```json
{
  "step": "ANALYZE|CHOOSE|DECOMPILE|EXECUTE|VERIFY|LEARN|ACCEPT|IMPOSSIBLE",
  "input": "...",
  "action": "...",
  "artifacts": ["..."],
  "result": "ok|fail|partial|accept|impossible",
  "next": "...",
  "memory_update": { "...": "..." },
  "detail": { "...": "..." },
  "iteration": 1
}
```

Reports are written to `<work-dir>/reports/NNN_STEP.json` and collected in
`<work-dir>/run_outcome.json`.

## Strategy catalogue

| Strategy | Applies to | Notes |
|---|---|---|
| `unity_il2cpp_ndk` | Unity IL2CPP | Il2CppDumper вҶ’ C++ вҶ’ NDK rebuild |
| `unity_mono_monoembed` | Unity Mono | embed Mono + managed assemblies |
| `unreal_android_ndk` | Unreal | first-party Android export |
| `godot_android` | Godot | first-party Android export |
| `dotnet_transpile` / `dotnet_mono_embed` | .NET | IL вҶ’ Kotlin, or embed Mono |
| `java_dex_repack` | Java | jadx + apktool repack |
| `gm_html5_wrap` | GameMaker | HTML5 runtime in a WebView |
| `rm_engine_port` | RPG Maker / Ren'Py | script/runtime port |
| `native_dbt_box64` | native x86/x86_64 | Box64 dynamic binary translation |
| `wine_winlator` | native + D3D | Wine + DXVK/VKD3D + Box64 |

## Memory model

`Memory` (`portagent/memory.py`) is SQLite with three tables:

- `attempts` вҖ“ every (profile-signature, strategy, verdict) outcome.
- `patches` вҖ“ reusable, successful patch vectors keyed by signature.
- `rules` вҖ“ `(engine, graphics_api) вҶ’ strategy` with running success/failure
  counts and a Laplace-smoothed confidence the planner uses as a boost.

## Verification philosophy

The verifier never overstates. It reports `UNKNOWN` when it cannot prove
equivalence (no oracle, no compiler), `FAIL` on any mismatch or
non-deterministic build, and `PASS` only on an exact per-scene trace match plus
a deterministic build.
