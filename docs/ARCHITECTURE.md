# Architecture

## Pipeline

```
[Input: installer / installed game folder]
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ PortAgent orchestrator (policy)                               │
│   Analyzer   – reconnaissance (PE/ELF, engine, APIs, DRM)      │
│   Planner    – CHOOSE strategy, weighted by Memory             │
│   Decompiler – recover code (monodis/objdump/jadx/Il2CppDumper)│
│   Translator – WinAPI/DirectX/audio → Android C shims          │
│   Builder    – Gradle/NDK project + native compile             │
│   Optimizer  – ABI/backend/heap/DBT plan                       │
│   Runtime    – pick runtime class + env, bundle payload        │
│   Packager   – real signed APK (aapt2/d8/zipalign/apksigner)   │
│   Verifier   – determinism + scene equivalence vs oracle       │
│   Memory     – SQLite: attempts, patch vectors, heuristic rules│
└───────────────────────────────────────────────────────────────┘
        │
        ▼
[Output: APK + equivalence report]  or  [IMPOSSIBLE + evidence]
```

## Loop

1. **ANALYZE** – walk the folder, parse every PE/ELF (`portagent/parsers.py`),
   score engine signatures, map imported DLLs to runtime APIs, detect DRM /
   anti-cheat, and record hard blockers. Produces `GameProfile` (JSON).
2. **CHOOSE** – `Planner` ranks candidate strategies from static engine/API
   rules plus learned weights from `Memory`. Hard blockers short-circuit to
   `UNSUPPORTED`.
3. **DECOMPILE** – `Decompiler` runs the real recovery tools for the title's
   class: `monodis` → IL for managed assemblies, `objdump` → native disassembly,
   `jadx` → Java from DEX, `Il2CppDumper` → metadata for Unity IL2CPP. The result
   (`DecompileResult`) records which tools ran and what was recovered.
4. **EXECUTE** – `Translator` writes a mapping table and three compilable C
   shims (`win32`, `gfx`, `audio`); `Builder` scaffolds a real Gradle/CMake
   Android project and host-compiles the shims; `Optimizer` decides ABI/backend/
   heap/DBT tuning; `RuntimePlanner` picks the runtime class and environment;
   `ApkPackager` builds a real, installable, signed APK (aapt2 → javac → d8 →
   zip → zipalign → apksigner), embedding the game under `assets/game/**`, the
   runtime config under `assets/game/portagent_runtime/`, and the shims as
   `lib/arm64-v8a/libportagent.so`.
5. **VERIFY** – `Verifier` compiles the shims twice (determinism), builds and
   runs a C scene harness across `menu → gameplay → save`, hashes each trace,
   and compares to the oracle. Only an exact match yields `PASS`.
6. **LEARN** – the outcome is written to `Memory` (attempt row + rule weight
   update + patch vector on success). The next iteration consults it.

Termination: `ACCEPT` (verified), `IMPOSSIBLE` (hard blocker), `exhausted`
(finite strategy space consumed), or `inconclusive` (iteration budget spent).

## Runtime layer

The packager's launcher Activity is an **embedded runtime layer** that dispatches
on a runtime class chosen from the profile and strategy:

| Class | Chosen when | Launch behaviour |
|---|---|---|
| `engine_native` | engine has a first-party Android exporter | run the engine's ARM64 runtime on the data pack |
| `mono_managed` | .NET/Mono title | boot Mono, load recovered assemblies |
| `dbt_box64` | native x86/x64 title | run the original exe under Box64 |
| `wine_dxvk` | native Windows + D3D title | Box64 + Wine + DXVK/VKD3D |

`RuntimePlanner` writes the class, entry point, backend, translation and env vars
(`PAGENT_ENGINE`, `PAGENT_STRATEGY`, `BOX64_*`, `DXVK_*`) to
`assets/game/portagent_runtime/env.json`. `RuntimeBundler` copies prebuilt ARM64
runtime payloads from `~/.portagent-tools/runtime/<class>/` (populated by
`scripts/fetch_runtimes.sh`) into the APK. When a payload is missing the app
reports the missing library by name rather than silently failing.

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
| `unity_il2cpp_ndk` | Unity IL2CPP | Il2CppDumper → C++ → NDK rebuild |
| `unity_mono_monoembed` | Unity Mono | embed Mono + managed assemblies |
| `unreal_android_ndk` | Unreal | first-party Android export |
| `godot_android` | Godot | first-party Android export |
| `dotnet_transpile` / `dotnet_mono_embed` | .NET | IL → Kotlin, or embed Mono |
| `java_dex_repack` | Java | jadx + apktool repack |
| `gm_html5_wrap` | GameMaker | HTML5 runtime in a WebView |
| `rm_engine_port` | RPG Maker / Ren'Py | script/runtime port |
| `native_dbt_box64` | native x86/x86_64 | Box64 dynamic binary translation |
| `wine_winlator` | native + D3D | Wine + DXVK/VKD3D + Box64 |

## Memory model

`Memory` (`portagent/memory.py`) is SQLite with three tables:

- `attempts` – every (profile-signature, strategy, verdict) outcome.
- `patches` – reusable, successful patch vectors keyed by signature.
- `rules` – `(engine, graphics_api) → strategy` with running success/failure
  counts and a Laplace-smoothed confidence the planner uses as a boost.

## Verification philosophy

The verifier never overstates. It reports `UNKNOWN` when it cannot prove
equivalence (no oracle, no compiler), `FAIL` on any mismatch or
non-deterministic build, and `PASS` only on an exact per-scene trace match plus
a deterministic build.
