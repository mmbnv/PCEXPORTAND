# AGENTS.md

Repository-specific knowledge for future agent sessions on **PCEXPORTAND**
(PortAgent: iterative PC-game → Android porter).

## What this repo is

A dependency-free Python package (`portagent/`) implementing the loop
`ANALYZE → CHOOSE → EXECUTE → VERIFY → LEARN`, plus tests (`tests/`), a pilot
(`scripts/pilot.py`), a toolchain bootstrap (`scripts/bootstrap_android.sh`),
and docs (`docs/`).

## Environment facts (verified)

- Base image: Debian 13, Python 3.13, `sudo` works, network works.
- No `/dev/kvm` → Android emulator is not usable here.
- Cross compilers installed for tests: `mingw-w64` (x86/x86_64 PE),
  `gcc-aarch64-linux-gnu` (ARM64 ELF). Install with
  `sudo apt-get install -y mingw-w64 gcc-aarch64-linux-gnu`.
- Android SDK/NDK are **not** installed by default; run
  `scripts/bootstrap_android.sh` (large download) if you need real APKs.

## Commands

```bash
python -m unittest discover -s tests -v     # full suite (builds real fixtures)
python scripts/pilot.py --out pilot          # end-to-end pilot over fixtures
python -m portagent analyze <game-folder>    # reconnaissance only
python -m portagent run <game> --oracle o.json
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
- `portagent/analyzer.py` – engine/API/DRM detection + impossibility.
- `portagent/planner.py` – strategy choice + memory weighting.
- `portagent/translator.py` – API mapping + generated C shims.
- `portagent/builder.py` – Gradle/NDK scaffold + compile + APK.
- `portagent/verifier.py` – determinism + scene equivalence.
- `portagent/memory.py` – SQLite experience store.
- `portagent/orchestrator.py` – the loop and JSON step reports.
