"""Desktop GUI for PortAgent (Tkinter, stdlib only).

A real window: pick a game folder, click Analyze / Build APK / Run loop, watch
progress, then copy or save the stats report to send back for support. Long
operations run on a worker thread so the window never freezes; progress is
marshalled back to the Tk main loop with ``after``.

Open it with ``python run.py`` (menu option 7 or ``--gui``) or directly:

    python -m portagent.gui
"""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from portagent import launcher

# Tkinter is optional: the CLI/menu still work without it. Only the desktop
# window needs it, so we import lazily and degrade with a clear message.
try:  # pragma: no cover - depends on the host having Tk
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    _TK_AVAILABLE = True
    _TK_ERROR = ""
except Exception as _exc:  # noqa: BLE001
    tk = None  # type: ignore[assignment]
    _TK_AVAILABLE = False
    _TK_ERROR = str(_exc)


class PortAgentGUI(tk.Tk if _TK_AVAILABLE else object):  # type: ignore[misc]
    def __init__(self) -> None:
        if not _TK_AVAILABLE:
            raise RuntimeError(f"Tkinter is not available: {_TK_ERROR}")
        super().__init__()
        self.title("PortAgent — PC game -> Android porter")
        self.geometry("900x640")
        self.minsize(760, 520)

        self.game: Path | None = None
        self.work: Path = Path.cwd() / "portagent_out"
        self.no_decompile = tk.BooleanVar(value=False)
        self.profile: dict | None = None
        self.summary: dict | None = None

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None

        self._build_widgets()
        self.after(100, self._drain_queue)

    # -- layout -----------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 4}

        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        self.game_var = tk.StringVar(value="(no game selected)")
        ttk.Label(top, text="Game:").pack(side="left")
        ttk.Label(top, textvariable=self.game_var, foreground="#0a6").pack(
            side="left", padx=6)
        ttk.Button(top, text="Choose folder…", command=self.choose_folder).pack(side="right")
        ttk.Button(top, text="Choose file…", command=self.choose_file).pack(
            side="right", padx=4)

        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        self.btn_analyze = ttk.Button(actions, text="1. Analyze", command=self.do_analyze)
        self.btn_build = ttk.Button(actions, text="2. Build APK", command=self.do_build)
        self.btn_loop = ttk.Button(actions, text="3. Run full loop", command=self.do_loop)
        for i, b in enumerate((self.btn_analyze, self.btn_build, self.btn_loop)):
            b.pack(side="left", padx=4)
        ttk.Checkbutton(actions, text="skip decompile",
                        variable=self.no_decompile).pack(side="left", padx=12)
        ttk.Button(actions, text="Settings…", command=self.open_settings).pack(side="right")

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", **pad)

        self.sdk_var = tk.StringVar(value="")
        self.sdk_label = ttk.Label(self, textvariable=self.sdk_var, foreground="#a00")
        self.sdk_label.pack(anchor="w", padx=10)
        self._refresh_sdk_status()

        self.rt_var = tk.StringVar(value="")
        self.rt_label = ttk.Label(self, textvariable=self.rt_var, foreground="#a00")
        self.rt_label.pack(anchor="w", padx=10)
        self._refresh_runtime_status()

        stats = ttk.LabelFrame(self, text="Stats")
        stats.pack(fill="x", **pad)
        self.stats_text = tk.Text(stats, height=9, wrap="word", state="disabled",
                                  background="#fbfbfb")
        self.stats_text.pack(fill="x", padx=6, pady=6)

        logf = ttk.LabelFrame(self, text="Log")
        logf.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(logf, height=12, wrap="word",
                                             state="disabled", background="#101418",
                                             foreground="#d7e0e6", insertbackground="#fff")
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", **pad)
        ttk.Button(bottom, text="Copy report", command=self.copy_report).pack(side="left")
        ttk.Button(bottom, text="Save report…", command=self.save_report).pack(
            side="left", padx=4)
        ttk.Button(bottom, text="Open output folder", command=self.open_output).pack(
            side="left", padx=4)
        ttk.Button(bottom, text="Quit", command=self.destroy).pack(side="right")

    # -- helpers ----------------------------------------------------------
    def _refresh_sdk_status(self) -> None:
        from portagent.packager import find_android_toolchain

        tc = find_android_toolchain()
        if tc:
            self.sdk_var.set(f"Android toolchain: ready ({tc.get('build_tools', '')})")
            self.sdk_label.configure(foreground="#0a6")
        else:
            self.sdk_var.set(
                "Android toolchain: NOT FOUND - set ANDROID_HOME to build an APK "
                "(Windows: scripts\\bootstrap_android.ps1)"
            )
            self.sdk_label.configure(foreground="#a00")

    def _refresh_runtime_status(self) -> None:
        from portagent import runtimes

        info = runtimes.info()
        if info.get("ready"):
            self.rt_var.set(
                f"ARM64 runtime: ready ({info.get('box64_version') or 'box64 + wine + dxvk'})"
            )
            self.rt_label.configure(foreground="#0a6")
        else:
            self.rt_var.set(
                "ARM64 runtime: NOT fetched - click 'Build APK' to download it once "
                "(~475 MB, Box64 + Wine + DXVK)"
            )
            self.rt_label.configure(foreground="#a00")

    def log_line(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_stats(self, data: dict) -> None:
        self.stats_text.configure(state="normal")
        self.stats_text.delete("1.0", "end")
        self.stats_text.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2))
        self.stats_text.configure(state="disabled")

    def choose_folder(self) -> None:
        chosen = filedialog.askdirectory(initialdir=str(self.game or Path.home()),
                                         title="Select the game folder")
        if chosen:
            self._set_game(Path(chosen))

    def choose_file(self) -> None:
        chosen = filedialog.askopenfilename(
            initialdir=str(self.game or Path.home()),
            title="Select the installer file",
            filetypes=[("Installers", "*.exe *.msi *.bin *.zip *.7z"), ("All", "*.*")])
        if chosen:
            self._set_game(Path(chosen))

    def _set_game(self, p: Path) -> None:
        self.game = p
        self.profile = None
        self.summary = None
        self.game_var.set(str(p))
        self.log_line(f"Selected: {p}")

    def _progress(self, stage: str, message: str) -> None:
        self._queue.put(("log", f"[{stage}] {message}"))

    def _busy(self, on: bool) -> None:
        state = "disabled" if on else "normal"
        for b in (self.btn_analyze, self.btn_build, self.btn_loop):
            b.configure(state=state)
        if on:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _run(self, label: str, fn) -> None:
        if self._worker and self._worker.is_alive():
            messagebox.showinfo("PortAgent", "A task is already running.")
            return
        self._busy(True)
        self.log_line(f"\n=== {label} ===")

        def work() -> None:
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - report any failure to the UI
                self._queue.put(("error", str(exc)))
            finally:
                self._queue.put(("done", None))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.log_line(str(payload))
                elif kind == "error":
                    self.log_line(f"ERROR: {payload}")
                    messagebox.showerror("PortAgent", str(payload))
                elif kind == "done":
                    self._busy(False)
                elif kind == "refresh_runtime":
                    self._refresh_runtime_status()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    # -- actions ----------------------------------------------------------
    def _require_game(self) -> bool:
        if not self.game:
            messagebox.showwarning("PortAgent", "Choose a game folder first.")
            return False
        return True

    def do_analyze(self) -> None:
        if not self._require_game():
            return

        def task() -> None:
            self.profile = launcher.analyze(self.game)
            p = self.profile
            self._queue.put(("log", f"engine={p['engine']} arch={p['arch']} "
                                    f"managed={p['managed']} il2cpp={p['il2cpp']} "
                                    f"feasible={p['feasible']}"))
            for b in p.get("blockers", []):
                self._queue.put(("log", f"blocker: {b.get('reason')}"))
            self._queue.put(("stats", p))

        self._run("ANALYZE", task)

    def do_build(self) -> None:
        if not self._require_game():
            return
        self._refresh_sdk_status()
        self.work.mkdir(parents=True, exist_ok=True)

        def task() -> None:
            from portagent import runtimes

            if not runtimes.runtime_ready():
                self._queue.put(("log", "ARM64 runtime missing - fetching it once "
                                        "(~475 MB, cached in ~/.portagent-tools) ..."))
                fetched = runtimes.fetch_runtimes(progress=self._progress)
                if not fetched.ok:
                    self._queue.put(("log", "runtime fetch did not fully succeed; "
                                            "the APK will still build but cannot launch"))
            self.summary = launcher.run_port(
                self.game, self.work, decompile=not self.no_decompile.get(),
                progress=self._progress, profile=None)
            self._queue.put(("log", f"summary: {self.summary.get('status')} "
                                    f"apk={self.summary.get('apk')}"))
            self._queue.put(("stats", self.summary))
            self._queue.put(("refresh_runtime", None))

        self._run("BUILD APK", task)

    def do_loop(self) -> None:
        if not self._require_game():
            return
        self.work.mkdir(parents=True, exist_ok=True)

        def task() -> None:
            self.summary = launcher.run_loop(self.game, self.work,
                                             progress=self._progress)
            self._queue.put(("log", f"loop outcome: {self.summary.get('status')}"))
            self._queue.put(("stats", self.summary))

        self._run("FULL LOOP", task)

    # -- reports ----------------------------------------------------------
    def _report_dict(self) -> dict:
        if not self.summary:
            return {}
        return launcher.build_report(self.summary, self.profile, include_profile=True)

    def copy_report(self) -> None:
        report = self._report_dict()
        if not report:
            messagebox.showinfo("PortAgent", "Nothing to report yet — run a build.")
            return
        md = launcher.format_markdown(report)
        self.clipboard_clear()
        self.clipboard_append(md + "\n```json\n" +
                              json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")
        self.log_line("Report copied to clipboard.")
        messagebox.showinfo("PortAgent", "Report copied to clipboard. Paste it to send back.")

    def save_report(self) -> None:
        report = self._report_dict()
        if not report:
            messagebox.showinfo("PortAgent", "Nothing to report yet — run a build.")
            return
        default = str(Path.cwd() / "portagent_report.json")
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            initialfile="portagent_report.json",
                                            initialdir=str(Path.cwd()))
        if not path:
            return
        Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        Path(path).with_suffix(".md").write_text(launcher.format_markdown(report),
                                                 encoding="utf-8")
        self.log_line(f"Saved report: {path}")
        messagebox.showinfo("PortAgent", f"Saved:\n{path}\n{Path(path).with_suffix('.md')}")

    def open_output(self) -> None:
        import subprocess
        import sys as _sys

        self.work.mkdir(parents=True, exist_ok=True)
        try:
            if _sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(self.work)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", str(self.work)])
            else:
                subprocess.Popen(["xdg-open", str(self.work)])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("PortAgent", f"Could not open folder: {exc}")

    def open_settings(self) -> None:
        win = tk.Toplevel(self)
        win.title("Settings")
        win.transient(self)
        ttk.Label(win, text="Work directory:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        work_var = tk.StringVar(value=str(self.work))
        ttk.Entry(win, textvariable=work_var, width=50).grid(row=0, column=1, padx=8)

        def browse() -> None:
            chosen = filedialog.askdirectory(initialdir=str(self.work))
            if chosen:
                work_var.set(chosen)

        ttk.Button(win, text="…", command=browse).grid(row=0, column=2, padx=4)

        def apply() -> None:
            self.work = Path(work_var.get()).expanduser()
            self.log_line(f"Work dir set to {self.work}")
            win.destroy()

        ttk.Button(win, text="Apply", command=apply).grid(row=1, column=1, sticky="e",
                                                          padx=8, pady=8)


def is_available() -> bool:
    """True when a Tk display is usable (mirrors the module import result)."""
    return _TK_AVAILABLE


def launch() -> int:
    """Create and run the GUI. Returns 0 on a clean exit."""
    if not _TK_AVAILABLE:
        print(f"PortAgent GUI unavailable: {_TK_ERROR}")
        print("Install Tkinter (e.g. 'apt-get install python3-tk') or use run.py's menu.")
        return 1
    try:
        app = PortAgentGUI()
    except Exception as exc:  # noqa: BLE001 - no display / broken Tk
        print(f"PortAgent GUI could not start: {exc}")
        return 1
    app.log_line("PortAgent GUI ready. Choose a game folder to begin.")
    app.mainloop()
    return 0


def main() -> int:
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
