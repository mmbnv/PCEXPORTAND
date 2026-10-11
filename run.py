#!/usr/bin/env python3
"""PortAgent interactive launcher — a friendly menu instead of CLI flags.

Run it with either:

    python run.py
    python -m portagent.gui          # same GUI, if you prefer the module path

It offers:
    1. Pick a game folder / installer  (native directory picker when available)
    2. Analyze it                       (engine, arch, blockers, feasibility)
    3. Build an APK                     (full ANALYZE..BUILD pipeline)
    4. Run the full loop                (ANALYZE..LEARN, optional oracle)
    5. Show the stats/report            (and copy or save it to send back)
    6. Change settings                  (work dir, no-decompile)
    7. Open the desktop GUI             (Tkinter window with the same actions)
    0. Quit

The menu is pure stdlib. Tkinter is used only for the optional GUI and file
pickers; when it is unavailable the menu falls back to text prompts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from portagent import launcher


# ---------------------------------------------------------------------------
# Small console helpers (no third-party deps, colourful where the terminal is)
# ---------------------------------------------------------------------------
class C:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty()


def _ask(prompt: str) -> str | None:
    """Read a line; return None on EOF/Ctrl-C so the menu can exit cleanly
    instead of crashing when stdin is not interactive (e.g. piped input)."""
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _wrap(text: str, color: str = "") -> str:
    return f"{color}{text}{C.RESET}" if _supports_color() and color else text


def _hr(char: str = "-", n: int = 68) -> None:
    print(_wrap(char * n, C.DIM))


def _title() -> None:
    _hr("=")
    print(_wrap("  PortAgent", C.BOLD) + "  " +
          _wrap("PC-game -> Android porter", C.CYAN))
    _hr("=")


STATUS_COLOR = {
    "ok": C.GREEN,
    "accepted": C.GREEN,
    "impossible": C.RED,
    "partial": C.YELLOW,
    "exhausted": C.YELLOW,
    "inconclusive": C.YELLOW,
}


def _status_tag(status: str) -> str:
    return _wrap(status, STATUS_COLOR.get(status, ""))


# ---------------------------------------------------------------------------
# Optional Tkinter pickers (graceful fallback)
# ---------------------------------------------------------------------------
def pick_directory(initial: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    chosen = filedialog.askdirectory(initialdir=initial or str(Path.home()),
                                     title="Select the game folder")
    root.destroy()
    return chosen or None


def pick_file(initial: str | None = None) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    root = tk.Tk()
    root.withdraw()
    chosen = filedialog.askopenfilename(
        initialdir=initial or str(Path.home()),
        title="Select the installer file",
        filetypes=[("Installers / executables", "*.exe *.msi *.bin *.zip *.7z"),
                   ("All files", "*.*")],
    )
    root.destroy()
    return chosen or None


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
class Session:
    def __init__(self) -> None:
        self.game: Path | None = None
        self.work: Path = Path.cwd() / "portagent_out"
        self.no_decompile = False
        self.oracle_path: Path | None = None
        self.profile: dict | None = None
        self.summary: dict | None = None

    def toolchain_ready(self) -> bool:
        from portagent.packager import find_android_toolchain

        return find_android_toolchain() is not None

    # -- path input -------------------------------------------------------
    def choose_game(self) -> None:
        print()
        print("Enter the path to the game folder or installer.")
        print(_wrap("Tip: leave it blank to open a folder picker dialog.", C.DIM))
        raw = _ask("Game path > ")
        if raw is None:
            return
        raw = raw.strip('"')
        if not raw:
            picked = pick_directory(str(self.game.parent) if self.game else None)
            if not picked:
                print(_wrap("No folder selected.", C.YELLOW))
                return
            raw = picked
        p = Path(raw).expanduser()
        if not p.exists():
            print(_wrap(f"Not found: {p}", C.RED))
            return
        self.game = p
        self.profile = None
        self.summary = None
        kind = "folder" if p.is_dir() else "installer/file"
        print(_wrap(f"Selected ({kind}): {p}", C.GREEN))

    # -- actions ----------------------------------------------------------
    def _progress(self, stage: str, message: str) -> None:
        print(f"  {_wrap(stage.ljust(9), C.CYAN)} {message}")

    def analyze(self) -> None:
        if not self.game:
            print(_wrap("Pick a game first (option 1).", C.YELLOW))
            return
        print()
        print(_wrap("Analyzing ...", C.BOLD))
        try:
            self.profile = launcher.analyze(self.game)
        except Exception as exc:  # noqa: BLE001 - surface any parse failure
            print(_wrap(f"Analysis failed: {exc}", C.RED))
            return
        p = self.profile
        print()
        print(f"  engine        : {_wrap(p['engine'], C.BOLD)} "
              f"(confidence {p.get('engine_confidence')})")
        print(f"  arch          : {p['arch']} ({p['bitness']}-bit)")
        print(f"  managed/il2cpp: {p['managed']} / {p['il2cpp']}")
        print(f"  graphics/audio: {p['graphics_api']} / {p['audio_api']}")
        print(f"  dependencies  : {len(p['dependencies'])} DLLs")
        if p.get("drm"):
            print(f"  drm           : {', '.join(p['drm'])}")
        if p.get("anticheat"):
            print(f"  anti-cheat    : {_wrap(', '.join(p['anticheat']), C.RED)}")
        feasible = p.get("feasible", True)
        verdict = _wrap("FEASIBLE", C.GREEN) if feasible else _wrap("IMPOSSIBLE", C.RED)
        print(f"  verdict       : {verdict}")
        if p.get("blockers"):
            for b in p["blockers"]:
                print(f"    {_wrap('blocker', C.RED)}: {b.get('reason')}")
        if p.get("notes"):
            for n in p["notes"]:
                print(f"    {_wrap('note', C.YELLOW)}: {n}")
        print(_wrap(f"\n  Full profile saved in the report (option 5).", C.DIM))

    def build(self) -> None:
        if not self.game:
            print(_wrap("Pick a game first (option 1).", C.YELLOW))
            return
        if not self.toolchain_ready():
            print()
            print(_wrap("Android SDK/NDK not found — a real APK cannot be built yet.", C.RED))
            print("Install it, then set ANDROID_HOME (or ANDROID_SDK_ROOT):")
            print(_wrap("  Windows:  powershell -ExecutionPolicy Bypass "
                        "-File scripts\\bootstrap_android.ps1", C.CYAN))
            print(_wrap("  Linux/macOS:  scripts/bootstrap_android.sh", C.CYAN))
            print(_wrap("Option 8 shows exactly what is missing. Analysis (option 2) "
                        "still works without the SDK.", C.DIM))
            return
        print()
        print(_wrap("Building APK ...", C.BOLD))
        print(_wrap(f"  work dir: {self.work}", C.DIM))
        self.work.mkdir(parents=True, exist_ok=True)
        try:
            self.summary = launcher.run_port(
                self.game, self.work, decompile=not self.no_decompile,
                progress=self._progress,
            )
        except Exception as exc:  # noqa: BLE001
            print(_wrap(f"Build failed: {exc}", C.RED))
            return
        self._print_summary()

    def loop(self) -> None:
        if not self.game:
            print(_wrap("Pick a game first (option 1).", C.YELLOW))
            return
        oracle = None
        if self.oracle_path and self.oracle_path.is_file():
            try:
                oracle = json.loads(self.oracle_path.read_text(encoding="utf-8"))
                print(_wrap(f"Using oracle: {self.oracle_path}", C.DIM))
            except Exception as exc:  # noqa: BLE001
                print(_wrap(f"Could not read oracle: {exc}", C.RED))
                return
        else:
            print(_wrap("No oracle set -> the verifier can only say UNKNOWN "
                        "(that is honest, not a failure).", C.DIM))
        print()
        print(_wrap("Running full loop (ANALYZE..LEARN) ...", C.BOLD))
        try:
            self.summary = launcher.run_loop(
                self.game, self.work, oracle=oracle, progress=self._progress
            )
        except Exception as exc:  # noqa: BLE001
            print(_wrap(f"Loop failed: {exc}", C.RED))
            return
        self._print_summary()

    def _print_summary(self) -> None:
        s = self.summary or {}
        print()
        _hr()
        print(f"  status        : {_status_tag(s.get('status', '-'))}")
        print(f"  engine        : {s.get('engine', '-')}")
        print(f"  strategy      : {s.get('strategy', '-')}")
        rt = s.get("runtime") or {}
        if rt:
            print(f"  runtime       : {rt.get('runtime_class', '-')}")
        if s.get("apk"):
            print(f"  apk           : {_wrap(s['apk'], C.GREEN)}")
            print(f"  size / signed : {s.get('apk_size', 0)} bytes / "
                  f"signed={s.get('signed')} aligned={s.get('aligned')}")
        if s.get("message"):
            print(f"  message       : {s['message']}")
        if s.get("steps"):
            print(f"  steps         : {s['steps']}")
        if s.get("status") == "fail":
            for line in (s.get("logs") or [])[-5:]:
                print(f"    {_wrap('log', C.RED)}: {line}")
        if s.get("timings"):
            print(f"  timings (s)   : {s['timings']}")
        _hr()

    def report(self) -> None:
        if not self.summary:
            print(_wrap("Nothing built yet — run option 2 or 3 first.", C.YELLOW))
            return
        report = launcher.build_report(self.summary, self.profile,
                                       include_profile=True)
        md = launcher.format_markdown(report)
        print()
        _hr("=")
        print(md)
        _hr("=")
        print("Save this report to send it back for support:")
        default = Path.cwd() / "portagent_report.json"
        raw = _ask(f"path [{default}] > ")
        if raw is None:
            return
        raw = raw.strip('"') or str(default)
        out = Path(raw).expanduser()
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        out.with_suffix(".md").write_text(md, encoding="utf-8")
        print(_wrap(f"Saved: {out}  and  {out.with_suffix('.md')}", C.GREEN))

    def settings(self) -> None:
        print()
        print("Settings")
        print(f"  1. Work dir          : {self.work}")
        print(f"  2. Skip decompile    : {self.no_decompile}")
        print(f"  3. Oracle file       : {self.oracle_path or '(none)'}")
        choice = _ask("Change which? [1/2/3, blank to go back] > ")
        if choice == "1":
            raw = _ask("New work dir > ")
            if raw:
                self.work = Path(raw.strip('"')).expanduser()
        elif choice == "2":
            self.no_decompile = not self.no_decompile
        elif choice == "3":
            raw = _ask("Oracle .json path (blank to clear) > ")
            if raw is None:
                return
            self.oracle_path = Path(raw.strip('"')).expanduser() if raw else None

    def show_env(self) -> None:
        env = launcher.environment()
        print()
        print(f"  Python   : {env['python']} on {env['platform']}")
        tc = env.get("android_toolchain")
        print(f"  Android  : {'ready' if tc else _wrap('NOT FOUND (set ANDROID_HOME)', C.RED)}")
        present = [k for k, v in env["tools"].items() if v]
        missing = [k for k, v in env["tools"].items() if not v]
        print(f"  present  : {', '.join(present) or 'none'}")
        print(f"  missing  : {', '.join(missing) or 'none'}")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
MENU = """
{title}
  game : {game}
  work : {work}
  sdk  : {sdk}
{hr}
  1. Choose game folder / installer
  2. Analyze
  3. Build APK
  4. Run full loop (needs oracle for PASS)
  5. Show stats / report  (save + send back)
  6. Settings
  7. Open desktop GUI
  8. Show environment / tools
  0. Quit
"""


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # "python run.py --gui" jumps straight to the window.
    if "--gui" in argv:
        return open_gui()

    sess = Session()
    # Allow: python run.py /path/to/game
    positional = [a for a in argv if not a.startswith("-")]
    if positional:
        p = Path(positional[0]).expanduser()
        if p.exists():
            sess.game = p

    while True:
        _title()
        sdk = (_wrap("ready", C.GREEN) if sess.toolchain_ready()
               else _wrap("MISSING - set ANDROID_HOME (see option 8)", C.RED))
        print(MENU.format(
            title=_wrap("Main menu", C.BOLD),
            game=sess.game or _wrap("(none selected)", C.YELLOW),
            work=sess.work,
            sdk=sdk,
            hr=_wrap("-" * 68, C.DIM),
        ))
        choice = _ask("Choose [0-8] > ")
        if choice is None:
            print(_wrap("Bye.", C.DIM))
            return 0
        if choice in ("0", "q", "quit", "exit"):
            print(_wrap("Bye.", C.DIM))
            return 0
        if choice == "1":
            sess.choose_game()
        elif choice == "2":
            sess.analyze()
        elif choice == "3":
            sess.build()
        elif choice == "4":
            sess.loop()
        elif choice == "5":
            sess.report()
        elif choice == "6":
            sess.settings()
        elif choice == "7":
            open_gui()
        elif choice == "8":
            sess.show_env()
        else:
            print(_wrap("Unknown option.", C.YELLOW))
        _ask(_wrap("\nPress Enter to continue...", C.DIM))


def open_gui() -> int:
    try:
        from portagent.gui import launch
    except Exception as exc:  # noqa: BLE001
        print(_wrap(f"GUI unavailable ({exc}). Use the menu instead.", C.YELLOW))
        return 1
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
