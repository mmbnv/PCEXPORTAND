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
- **Zip entry names must be written verbatim.** `zipfile.ZipFile.write()` →
  `ZipInfo.from_file()` runs the arcname through `os.path.normpath`, which
  rewrites `/` to `\` on Windows. A `\`-named entry (`assets\runtime\...`)
  breaks Android asset lookup and looks like a malformed package on install.
  `_assemble` therefore builds `ZipInfo` objects directly and stores names with
  `/`; `_validate_entries` refuses to report `ok` for any name with a backslash
  or an unsafe path. The regression test poisons `ZipInfo.from_file` to catch a
  return to `write()`.
- Assets are deflated (`AssetManager.open()` is compression-agnostic; the
  loader never uses `openFd()`), which takes the pilot APK from ~560 MB to
  ~170 MB. Native `.so` and `classes.dex` stay stored for `zipalign -p`.

## One-folder bundle (everything in one place)

`scripts/make_bundle.sh [OUT_DIR]` assembles a single self-contained folder
(default `dist/portagent-bundle/`) that contains the code *and* every external
tool, so a user can move one directory to another machine and run it with no
setup:

```
dist/portagent-bundle/
├── portagent/  run.py  docs/  scripts/     # the code
├── tools/android-sdk/   # aapt2, d8, zipalign, apksigner, android.jar, NDK clang
├── tools/jadx/  tools/il2cppdumper/  tools/apktool.jar
├── tools/runtime/wine_dxvk/               # Box64 + Wine + DXVK + Turnip (~475 MB)
├── install.sh  START.sh                   # Linux/macOS
└── install.bat START.bat                  # Windows
```

- `install.sh` / `install.bat` write `env.sh` / `env.bat`
  (`PAGENT_TOOLS_HOME`, `ANDROID_HOME`, `ANDROID_NDK_HOME`, `PATH`,
  `PYTHONPATH`) and print an OK/MISS checklist. `START.sh` / `START.bat` source
  it and run `run.py`. No network and no system-wide install are required.
- `portagent/toolpaths.py` is the single resolver for tool locations. Order:
  explicit env (`PAGENT_TOOLS_HOME`, `ANDROID_HOME`, `ANDROID_SDK_ROOT`,
  `ANDROID_NDK_HOME`) → `<repo>/tools` (the bundle) → `~/.portagent-tools`.
  This is why the bundle works with zero configuration; an env var still wins.
- Tests that assert "no SDK" must isolate the environment by pointing
  `PAGENT_TOOLS_HOME` at an empty temp dir, otherwise the bundle/`tools/`
  fallback (or the real cache) makes an SDK appear.
- `dist/` is git-ignored. Never commit the ~3.4 GB bundle; rebuild it with the
  script instead.

## Bootstrapping a toolchain (no installers)

`python -m portagent bootstrap` (menu option 9, GUI "Install toolchain") is the
supported way to get a working Android toolchain on a clean machine. It is pure
Python — it does **not** shell out to `apt`, `winget`, or `choco`:

1. find an existing JDK (`JAVA_HOME` or an already-unpacked Temurin in the
   tools home); otherwise download Temurin 17 from the Adoptium API. The API
   *redirects to a `.tar.gz`* even though the cached file is named `jdk.zip`, so
   `_extract_archive` picks the reader by magic bytes (gzip/bzip2 → tar, `PK` →
   zip) instead of trusting the suffix — trusting it raised
   "File is not a zip file";
2. download the Android `commandlinetools` zip, fix up the Unix executable bit
   (Python's `zipfile` drops it — without this `sdkmanager` is not runnable),
   then run `sdkmanager --licenses` and install
   `platform-tools`, `build-tools;34.0.0`, `platforms;android-34`, and
   `ndk;26.1.10909125`;
3. write `env.json` into the tools home and export the values into the process.

`portagent/toolpaths.py` auto-loads `<tools_home>/env.json` (via
`_load_bootstrap_env`) so the *next* command finds the SDK with no `setx` or
`export`; explicit shell variables always win (`os.environ.setdefault`).

On Windows the packager invokes `.bat`/`.cmd` through `cmd.exe` (CreateProcess
cannot exec them directly), and `javac`/`keytool` are resolved through
`JAVA_HOME` first via `packager._java_tool`. `packager._run` also prepends
`$JAVA_HOME/bin` to `PATH` for every child, because the build-tools wrappers
(`d8`, `apksigner`) `exec` a bare `java`; without this the APK build fails at
`d8` on a machine whose JDK was only bootstrapped. Use `os.path`, not `pathlib`,
when building that path: tests simulate Windows by patching `os.name`, which
flips the `pathlib` flavour to `WindowsPath` and raises `UnsupportedOperation`
on Linux.

Windows ships a JDK *shim* directory (`...\Common Files\Oracle\Java\javapath`)
that puts `javac` on PATH without being a JDK home; users often also have a
stale `JAVA_HOME` pointing at its parent. Passing either to `sdkmanager` aborts
with "JAVA_HOME is set to an invalid directory". `toolpaths.is_valid_java_home`
rejects anything lacking `bin/javac`, `ensure_jdk` ignores a shim `javac`/bogus
`JAVA_HOME` and downloads Temurin instead, and `_load_bootstrap_env` lets the
bootstrapped `JAVA_HOME` override an invalid one in the shell.

`portagent/runtimes.py` pins the Winlator release; the release asset is
`Winlator_11.2.apk` (no trailing `.0`). If the pinned URL ever 404s,
`_resolve_winlator_url` falls back to the GitHub releases API so the fetch
self-heals instead of hard-failing.

## Device-aware optimization

`portagent/device.py` derives a `DeviceProfile` (model, SoC, GPU vendor, RAM,
panel, ABI) and a `DeviceTuning` (render target, FPS cap, heap, Box64 dynarec
mode, Vulkan driver). `resolve(spec)` accepts `auto` (adb device, else this
machine), an inline `key=value,key=value` string, or a JSON path;
`Optimizer.plan(..., device=...)` merges the tuning env into the plan and
`ApkPackager` bakes it into the generated Java. CLI: `port`/`portall
--device <spec>` and the `device` subcommand to preview a profile.

Two hard-won facts that RAM alone gets wrong:

- **GPU vendor decides the driver.** Turnip is Adreno-only (`freedreno`), so a
  Mali device (e.g. Tecno Spark 10 Pro, Helio G88 / Mali-G52) must NOT get the
  bundled Turnip ICD. `device.DRIVER_FOR_VENDOR` maps Mali/PowerVR → system
  Vulkan (or VirGL/Zink), Adreno/Xclipse → turnip.
- **RAM over-tiers weak SoCs.** An 8 GB Helio G88 is "high" by RAM but cannot
  push a 1440p DXVK frame, so `_render_tier` caps known weak SoCs to the mid
  render target (1080 long edge). The panel is *not* rendered 1:1 — the game
  renders at the tuning target and is scaled up; that is what keeps a
  mid-range phone playable.

## The grey/black screen (why it happens)

The bundled Wine rootfs ships only `winex11.drv`, and there is **no X server**
binary in the bundle, so Wine cannot create a window → the grey/black screen
the user saw. Winlator's X server (`com.winlator.xserver.*`) is a *pure-Java*
component packaged inside its APK, not a standalone executable, so it does not
come along with the fetched `assets/` payload. The generated `PortService`
therefore looks for a display server (`findXServer`) and, if absent, reports it
by name and refuses to launch Wine — it never fakes a running game. Bundling a
real renderer (virgl `libvirglrenderer.so` + a Java X server) is the remaining
work to make rendering actually start.

## Runtime payload pruning

`RuntimeBundler.bundle(..., prune=True)` skips docs/man/locales/`*.a`/`*.h`/
`winedbg*` etc. This trims only a few percent of the ~558 MB Wine stack (most
weight is the `rootfs/opt/wine` tree itself), and the executables and `.so`
files are never touched. The biggest APK-size lever is the game payload, which
is copied verbatim.
