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
│   Translator – WinAPI/DirectX/audio → Android C shims          │
│   Builder    – Gradle/NDK project + native compile + APK       │
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
3. **EXECUTE** – `Translator` writes a mapping table and three compilable C
   shims (`win32`, `gfx`, `audio`); `Builder` scaffolds a real Gradle/CMake
   Android project, host-compiles the shims, cross-compiles for `arm64-v8a`
   when the NDK is present, and runs `gradle assembleDebug`.
4. **VERIFY** – `Verifier` compiles the shims twice (determinism), builds and
   runs a C scene harness across `menu → gameplay → save`, hashes each trace,
   and compares to the oracle. Only an exact match yields `PASS`.
5. **LEARN** – the outcome is written to `Memory` (attempt row + rule weight
   update + patch vector on success). The next iteration consults it.

Termination: `ACCEPT` (verified), `IMPOSSIBLE` (hard blocker), `exhausted`
(finite strategy space consumed), or `inconclusive` (iteration budget spent).

## Step report contract

Every stage emits the JSON required by the brief:

```json
{
  "step": "ANALYZE|CHOOSE|EXECUTE|VERIFY|LEARN|ACCEPT|IMPOSSIBLE",
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
