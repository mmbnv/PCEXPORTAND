"""Reusable, headless PortAgent API shared by the CLI, the interactive menu
(``run.py``) and the desktop GUI (``portagent/gui.py``).

Everything here is plain Python (no Tkinter), so it is importable and testable
without a display. The GUI and the menu only orchestrate these functions and
render their results.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .analyzer import Analyzer
from .models import GameProfile

ProgressFn = Callable[[str, str], None]


def _emit(progress: ProgressFn | None, stage: str, message: str) -> None:
    if progress is not None:
        try:
            progress(stage, message)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Environment / toolchain report
# ---------------------------------------------------------------------------
def environment() -> dict[str, Any]:
    """What the host can actually do — included in every exported report."""
    from .packager import find_android_toolchain

    def _which(name: str) -> str | None:
        return shutil.which(name)

    toolchain = find_android_toolchain()
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "tools": {
            name: _which(name)
            for name in (
                "objdump", "readelf", "monodis", "mono", "mcs", "jadx",
                "apktool", "javac", "aapt2", "d8", "zipalign", "apksigner",
                "x86_64-w64-mingw32-gcc", "aarch64-linux-gnu-gcc",
                "clang", "gradle",
            )
        },
        "android_toolchain": toolchain,
    }


# ---------------------------------------------------------------------------
# ANALYZE
# ---------------------------------------------------------------------------
def analyze(game: str | Path) -> dict[str, Any]:
    profile = Analyzer(game).analyze()
    return profile.to_dict()


# ---------------------------------------------------------------------------
# PORT (one command -> signed APK)  +  optionally the full loop
# ---------------------------------------------------------------------------
def run_port(
    game: str | Path,
    work_dir: str | Path,
    output: str | Path | None = None,
    decompile: bool = True,
    progress: ProgressFn | None = None,
    profile: GameProfile | None = None,
    device: str | None = None,
) -> dict[str, Any]:
    """Build an installable APK for ``game`` and return a summary dict.

    ``device`` selects the tuning target: None/'auto' auto-detects a connected
    adb device (else the local machine), a path to a prop JSON, or inline
    ``key=value`` props.

    Raises FileNotFoundError if the game path does not exist.
    """
    from .decompiler import Decompiler
    from .optimizer import Optimizer
    from .packager import ApkPackager, find_android_toolchain
    from .planner import Planner
    from .runtime import RuntimePlanner
    from .translator import Translator

    game = Path(game)
    if not game.exists():
        raise FileNotFoundError(f"no such file or directory: {game}")
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)

    toolchain = find_android_toolchain()
    if toolchain is None:
        # Fail fast with an actionable reason instead of a generic build error.
        msg = (
            "Android SDK/NDK not found. Run the built-in bootstrap to download a "
            "JDK + Android SDK/NDK with no system installers required:\n"
            "    python -m portagent bootstrap\n"
            "then re-run. (Advanced: set ANDROID_HOME/ANDROID_SDK_ROOT to an "
            "existing SDK that contains build-tools/ and platforms/android-*/android.jar.)"
        )
        _emit(progress, "EXECUTE", msg)
        return {
            "status": "fail",
            "engine": None,
            "apk": None,
            "message": msg,
            "steps": {"toolchain": "missing"},
            "logs": [msg],
            "timings": {},
            "environment": environment(),
        }
    _emit(progress, "EXECUTE",
          f"Android toolchain: {toolchain.get('build_tools')}")

    timings: dict[str, float] = {}
    t0 = time.time()

    _emit(progress, "ANALYZE", f"scanning {game} ...")
    if profile is None:
        profile = Analyzer(game).analyze()
    timings["analyze"] = round(time.time() - t0, 2)
    _emit(progress, "ANALYZE",
          f"engine={profile.engine.value} arch={profile.arch} "
          f"managed={profile.managed} il2cpp={profile.il2cpp} feasible={profile.feasible}")

    if not profile.feasible:
        return {
            "status": "impossible",
            "engine": profile.engine.value,
            "blockers": [b.to_dict() for b in profile.blockers],
            "message": "hard blocker: no Android-compatible path exists for this title",
            "timings": timings,
            "environment": environment(),
        }

    _emit(progress, "CHOOSE", "ranking strategies ...")
    strategy = Planner().choose(profile)[0].name
    _emit(progress, "CHOOSE", f"chosen strategy: {strategy.value}")

    decompile_result = None
    if decompile:
        t = time.time()
        _emit(progress, "DECOMPILE", "recovering code ...")
        decompile_result = Decompiler(work / "decompiled").decompile(profile)
        timings["decompile"] = round(time.time() - t, 2)
        _emit(progress, "DECOMPILE", f"status={decompile_result.status} "
                                     f"tools={decompile_result.tools_used}")
    else:
        _emit(progress, "DECOMPILE", "skipped (--no-decompile)")

    t = time.time()
    _emit(progress, "EXECUTE", "translating APIs -> C shims ...")
    translated = Translator(work / "translated").translate(profile, strategy)
    from .device import describe as describe_device
    from .device import resolve as resolve_device
    from .device import tune as tune_device

    dev = resolve_device(device)
    plan = Optimizer().plan(profile, strategy, device=dev)
    runtime = RuntimePlanner().plan(profile, strategy, plan)
    _emit(progress, "CHOOSE", describe_device(dev, tune_device(dev)))
    _emit(progress, "EXECUTE",
          f"runtime={runtime.runtime_class} backend={plan.graphics_backend} "
          f"dbt={plan.dbt} heap={plan.heap_mb}MB render={plan.render_width}x"
          f"{plan.render_height} fps_cap={plan.fps_cap}")

    _emit(progress, "EXECUTE", "packaging signed APK (aapt2/d8/zipalign/apksigner) ...")
    label = game.name if game.is_dir() else game.stem
    result = ApkPackager(work).package(game, translated, plan, app_label=label, runtime=runtime)
    timings["build"] = round(time.time() - t, 2)

    apk_out: Path | None = None
    if result.apk:
        apk_out = Path(output) if output else work / "portagent.apk"
        if str(apk_out) != result.apk:
            shutil.copy2(result.apk, apk_out)

    _emit(progress, "VERIFY", "signed=%s aligned=%s" % (result.signed, result.aligned))

    failed_steps = [k for k, v in result.steps.items() if str(v).startswith("fail")]
    if result.status == "ok":
        if runtime.runtime_class == "wine_dxvk" and result.runtime_bundled:
            message = (
                "emulation container built: installable signed APK with the ARM64 "
                "Box64 + Wine + DXVK stack and the game payload. Launches the title "
                "on an arm64-v8a device; frame-accurate equivalence is not verified "
                "without an on-device oracle trace."
            )
        elif runtime.runtime_class == "wine_dxvk":
            message = (
                "APK built, but the ARM64 runtime stack is NOT installed, so the "
                "container cannot launch the game. Run "
                "`python -m portagent.runtimes` to fetch Box64 + Wine + DXVK, then rebuild."
            )
        else:
            message = "installable signed APK produced"
    elif result.status == "partial":
        message = "APK built but not fully signed; see logs"
    else:
        message = (
            f"APK build failed at: {', '.join(failed_steps)}"
            if failed_steps
            else "APK build failed; see logs (is ANDROID_HOME set?)"
        )

    summary = {
        "status": "ok" if result.status == "ok" else result.status,
        "engine": profile.engine.value,
        "strategy": strategy.value,
        "apk": str(apk_out) if apk_out else None,
        "apk_size": result.apk_size,
        "signed": result.signed,
        "aligned": result.aligned,
        "abis": result.abis,
        "assets": {"files": result.asset_files, "bytes": result.asset_bytes},
        "runtime": runtime.to_dict(),
        "runtime_bundled": result.runtime_bundled,
        "decompile": decompile_result.to_dict() if decompile_result else {"status": "skipped"},
        "optimization": plan.to_dict(),
        "device": dev.to_dict(),
        "steps": result.steps,
        "logs": result.logs[-20:],
        "timings": timings,
        "environment": environment(),
        "message": message,
    }
    _emit(progress, "VERIFY", summary["message"])
    return summary


def run_loop(
    game: str | Path,
    work_dir: str | Path,
    oracle: dict | None = None,
    max_iterations: int = 3,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Run the full ANALYZE..LEARN loop and return the outcome dict."""
    from .orchestrator import PortAgent

    _emit(progress, "LOOP", f"ANALYZE..LEARN, max_iterations={max_iterations}")
    agent = PortAgent(
        work_dir=work_dir,
        memory_path=str(Path(work_dir) / "memory.sqlite"),
        oracle=oracle,
        max_iterations=max_iterations,
    )
    outcome = agent.run(str(game))
    out = outcome.to_dict()
    prof = out.get("profile") or {}
    out.setdefault("engine", prof.get("engine"))
    out["environment"] = environment()
    _emit(progress, "LOOP", f"status={outcome.status}")
    if out.get("message"):
        _emit(progress, "LOOP", out["message"])
    return out


# ---------------------------------------------------------------------------
# Report export (so the user can send a paste-friendly summary back)
# ---------------------------------------------------------------------------
def build_report(summary: dict[str, Any], profile: dict | None = None,
                 include_profile: bool = False) -> dict[str, Any]:
    """A compact report suitable for copying/sending for support."""
    report: dict[str, Any] = {
        "portagent_report_version": 1,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "summary": summary,
    }
    if include_profile and profile is not None:
        report["profile"] = profile
    return report


def format_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    env = s.get("environment", {}) or report.get("environment", {})
    lines = [
        "# PortAgent report",
        "",
        f"- Generated: {report.get('generated', '-')}",
        f"- Status: **{s.get('status', '-')}**",
        f"- Engine: {s.get('engine', '-')}",
        f"- Strategy: {s.get('strategy', '-')}",
    ]
    rt = s.get("runtime") or {}
    if rt:
        lines.append(f"- Runtime class: {rt.get('runtime_class', '-')}")
    if s.get("message"):
        lines.append(f"- Reason: {s['message']}")
    if s.get("apk"):
        lines += [
            f"- APK: `{s.get('apk')}`",
            f"- Size: {s.get('apk_size', 0)} bytes",
            f"- Signed: {s.get('signed')}  Aligned: {s.get('aligned')}",
            f"- ABIs: {', '.join(s.get('abis', []))}",
        ]
    if s.get("blockers"):
        lines.append("- Blockers:")
        for b in s["blockers"]:
            lines.append(f"  - {b.get('reason', b)}")
    if s.get("steps"):
        lines.append(f"- Steps: {json.dumps(s['steps'])}")
    if s.get("status") == "fail" and s.get("logs"):
        lines += ["", "## Failure logs", ""]
        for line in s["logs"][-10:]:
            lines.append("```")
            lines.append(str(line))
            lines.append("```")
    if s.get("timings"):
        lines.append(f"- Timings (s): {json.dumps(s['timings'])}")
    if env:
        lines += ["", "## Environment", "",
                  f"- Python: {env.get('python', '-')} on {env.get('platform', '-')}",
                  f"- Android toolchain: {'yes' if env.get('android_toolchain') else 'NO'}"]
        tools = env.get("tools", {})
        present = [k for k, v in tools.items() if v]
        missing = [k for k, v in tools.items() if not v]
        lines.append(f"- Tools present: {', '.join(present) if present else 'none'}")
        if missing:
            lines.append(f"- Tools missing: {', '.join(missing)}")
        if not env.get("android_toolchain"):
            lines += ["", "## Next step", "",
                      "No Android SDK/NDK was found, so no strategy can build an APK. "
                      "Install the toolchain with the built-in bootstrap (downloads a "
                      "JDK + Android SDK/NDK; no system installer required) and re-run:"]
            lines += ["", "```", "python -m portagent bootstrap", "```", ""]
            lines.append("Then re-run the loop. See `docs/USAGE.md` section 7 for details.")
    return "\n".join(lines) + "\n"
