# AGENTS.md

Repository-specific knowledge for future agent sessions on **PCEXPORTAND**
(PortAgent: iterative PC-game → Android porter).

## What this repo is

A dependency-free Python package (`portagent/`) implementing the loop
`ANALYZE → CHOOSE → EXECUTE → VERIFY → LEARN`, plus tests (`tests/`), a pilot
(`scripts/pilot.py`), a toolchain bootstrap (`scripts/bootstrap_android.sh`),
and docs (`docs/`).

## Environment facts (verified)

- Base image: Debian 13, Python 3.13, network works. `sudo`/`apt` may be
  unavailable in some sandboxes - rely on `pip` and downloadable binaries.
- No `/dev/kvm` → Android emulator is not usable here.
- Cross compilers installed for tests: `mingw-w64` (x86/x86_64 PE),
  `gcc-aarch64-linux-gnu` (ARM64 ELF). Install with
  `sudo apt-get install -y mingw-w64 gcc-aarch64-linux-gnu`.
- Android SDK/NDK are **not** installed by default; run
  `scripts/bootstrap_android.sh` (large download) if you need real APKs.
- To validate the ARM64 launch chain without a device: extract `qemu-aarch64`
  from the `qemu-user-static` `.deb` (no root) and run
  `qemu-aarch64 -L <rootfs> <rootfs>/usr/lib/ld-linux-aarch64.so.1 <box64> ...`.
  `loader -> box64 -> wine --version` prints `wine-10.10`. `wineboot`/PE init
  then hits `virtual_alloc_first_teb ... c0000018`, which is a **qemu artifact**
  (qemu reserves low address space); on a real arm64-v8a device Box64 runs
  natively and the game starts.

## Commands

```bash
python -m unittest discover -s tests -v     # full suite (builds real fixtures)
python scripts/pilot.py --out pilot          # end-to-end pilot over fixtures
python run.py                                # interactive menu (pick folder -> build -> report)
python run.py --gui  |  python -m portagent gui   # desktop GUI (Tkinter, optional)
python -m portagent analyze <game-folder>    # reconnaissance only
python -m portagent run <game> --oracle o.json
python -m portagent port <game> -o out.apk   # one command -> signed APK
python -m portagent portall --work-dir ./all # every strategy class -> summary
python -m portagent memory --memory <db>
```

## Conventions & gotchas

- **Stdlib only** for the package. Do not add third-party runtime deps.
- Generated C (Translator output) must compile with plain C11 and
  `-Wall -Wextra`. The Win32 shim needs `#define _POSIX_C_SOURCE 200809L`.
- Fixtures are *real* binaries compiled at test time. When referencing a DLL to
  force it into the PE import table, call a real symbol using the mingw SDK
  headers (`d3d11.h`, `xaudio2.h`, `mmsound.h`, `dsound.h`, ...). Ad-hoc
  prototypes conflict with the headers. `winsock2.h` must precede `windows.h`.
- PE data directories start at optional-header offset `0x70` (PE32+) / `0x60`
  (PE32) — a common off-by-`14*8` mistake breaks import parsing.
- The verifier must never report `PASS` without an oracle; keep the
  `UNKNOWN` path intact.
- Detected DRM notes are human-readable strings; match blockers on the
  signature key, not the note text.

## Where things live

- `portagent/parsers.py` – PE/ELF parsing.
- `portagent/launcher.py` – shared headless API (analyze/run_port/run_loop/report) used by run.py & GUI.
- `portagent/gui.py` – Tkinter desktop window (optional; degrades gracefully without Tk).
- `run.py` (repo root) – interactive console menu + `--gui` shortcut.
- `portagent/analyzer.py` – engine/API/DRM detection + impossibility.
- `portagent/planner.py` – strategy choice + memory weighting.
- `portagent/translator.py` – API mapping + generated C shims.
- `portagent/decompiler.py` – real code recovery (monodis/objdump/jadx/Il2CppDumper).
- `portagent/optimizer.py` – Android tuning (ABI/backend/heap/DBT) + env.
- `portagent/runtime.py` - runtime-class planning + payload bundling.
- `portagent/runtimes.py` - fetches/unpacks the pinned ARM64 stack
  (Box64 + Wine + DXVK + Turnip) from Winlator 11.2.0 into
  `~/.portagent-tools/runtime/wine_dxvk/`; verifies SHA-256 + ELF arch.
- `portagent/packager.py` – real signed APK (aapt2/javac/d8/zipalign/apksigner).
- `portagent/builder.py` – Gradle/NDK scaffold + host compile (legacy path).
- `portagent/verifier.py` – determinism + scene equivalence.
- `portagent/memory.py` – SQLite experience store.
- `portagent/orchestrator.py` – the loop and JSON step reports.

## Packaging notes

- The APK is built **without Gradle and without network**: `aapt2 compile` ->
  `aapt2 link` -> `javac` -> `d8` -> zip -> `zipalign` -> `apksigner`. This is
  deliberate so it works in an offline container.
- Toolchain discovery is in `packager.find_android_toolchain()`; it reads
  `ANDROID_HOME`/`ANDROID_SDK_ROOT` and picks the highest build-tools with both
  `aapt2` and `d8`, plus any `platforms/android-*/android.jar`.
- The game payload goes to `assets/game/**`; `PortActivity` (generated) unpacks
  it on first run. The native shims compile to `lib/arm64-v8a/libportagent.so`
  with NDK clang; the APK is still valid if the native step is skipped.
- The packager also writes `assets/game/portagent_runtime/env.json` (runtime
  class + env) whenever a `RuntimeConfig` is passed. `PortActivity` generates a
  `launchRuntime()` that dispatches on `RUNTIME_CLASS`
  (`engine_native|mono_managed|dbt_box64|wine_dxvk`). Heavy ARM64 runtime
  payloads (Box64/Wine/DXVK) are **not** vendored:
  `python -m portagent fetch-runtimes` (or `scripts/fetch_runtimes.sh`)
  installs them to `~/.portagent-tools/runtime/wine_dxvk/` and
  `RuntimeBundler` copies them to `assets/runtime/**`. Never fake a
  runtime being present.
- For source-less titles the runtime layer always picks `wine_dxvk`. The
  generated `PortActivity` (a) launches Box64 through the shipped aarch64
  loader (box64's own PT_INTERP points at Winlator's package), (b) sets
  `WINELOADERNOEXEC=1` + `WINEDLLPATH` (the wine stub execs a build-path
  preloader that we do not ship), (c) stages dxvk/vkd3d DLLs into the prefix
  with `WINEDLLOVERRIDES=...=n,b`, and (d) rewrites the Turnip ICD path.
- Tests for the packager are skipped (not faked) when the SDK is absent.
- `*.apk` is git-ignored — never commit build outputs.
