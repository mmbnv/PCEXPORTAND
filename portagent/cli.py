"""Command-line entry point: ``python -m portagent ...``."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .analyzer import Analyzer
from .orchestrator import PortAgent


def _cmd_analyze(args: argparse.Namespace) -> int:
    profile = Analyzer(args.game).analyze()
    print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
    return 0 if profile.feasible else 3


def _cmd_run(args: argparse.Namespace) -> int:
    oracle = None
    if args.oracle:
        path = Path(args.oracle)
        if not path.is_file():
            print(json.dumps({
                "status": "error",
                "message": f"oracle file not found: {path}",
                "hint": "Run without --oracle to build and get UNKNOWN, or pass a real "
                        "reference trace JSON (see docs/USAGE.md section on the oracle).",
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
        oracle = json.loads(path.read_text(encoding="utf-8"))
    agent = PortAgent(
        work_dir=args.work_dir,
        memory_path=args.memory,
        oracle=oracle,
        max_iterations=args.max_iterations,
    )
    outcome = agent.run(args.game)
    out = outcome.to_dict()
    # Surface the built APK at the work-dir root for convenience.
    if outcome.apk and Path(outcome.apk).exists():
        dest = Path(args.work_dir) / "portagent.apk"
        if str(dest) != outcome.apk:
            shutil.copy2(outcome.apk, dest)
        out["apk"] = str(dest)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return {"accepted": 0, "impossible": 3, "exhausted": 4, "inconclusive": 5}.get(outcome.status, 1)


def _cmd_port(args: argparse.Namespace) -> int:
    from .decompiler import Decompiler
    from .optimizer import Optimizer
    from .packager import ApkPackager, find_android_toolchain
    from .planner import Planner
    from .runtime import RuntimePlanner
    from .translator import Translator

    game = Path(args.game)
    if not game.exists():
        raise FileNotFoundError(f"no such file or directory: {game}")
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)

    toolchain = find_android_toolchain()
    if toolchain is None:
        out = {
            "status": "fail",
            "apk": None,
            "steps": {"toolchain": "missing"},
            "message": (
                "Android SDK/NDK not found. Set ANDROID_HOME (or ANDROID_SDK_ROOT) to a "
                "folder with build-tools/ and platforms/android-*/android.jar, then retry. "
                "On Windows run scripts/bootstrap_android.ps1 first."
            ),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 2

    profile = Analyzer(game).analyze()
    if not profile.feasible:
        out = {
            "status": "impossible",
            "engine": profile.engine.value,
            "blockers": [b.to_dict() for b in profile.blockers],
            "message": "hard blocker: no Android-compatible path exists for this title",
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 3

    planner = Planner()
    strategies = planner.choose(profile)
    strategy = strategies[0].name
    label = game.name if game.is_dir() else game.stem

    decompile = None
    if not args.no_decompile:
        decompile = Decompiler(work / "decompiled").decompile(profile)

    translated = Translator(work / "translated").translate(profile, strategy)
    plan = Optimizer().plan(profile, strategy)
    runtime = RuntimePlanner().plan(profile, strategy, plan)
    result = ApkPackager(work).package(
        game, translated, plan, app_label=label, runtime=runtime
    )

    apk_out = None
    if result.apk:
        apk_out = Path(args.output) if args.output else work / "portagent.apk"
        if str(apk_out) != result.apk:
            shutil.copy2(result.apk, apk_out)

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
        "decompile": decompile.to_dict() if decompile else {"status": "skipped"},
        "optimization": plan.to_dict(),
        "steps": result.steps,
        "message": (
            "installable signed APK produced"
            if result.status == "ok"
            else "APK built but not fully signed; see logs"
            if result.status == "partial"
            else "APK build failed; see logs (is ANDROID_HOME set?)"
        ),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result.status == "ok" else 1


def _port_one(game: Path, work: Path, decompile: bool = True) -> dict:
    """Run the full single-title pipeline and return a summary dict."""
    from .decompiler import Decompiler
    from .optimizer import Optimizer
    from .packager import ApkPackager
    from .planner import Planner
    from .runtime import RuntimePlanner
    from .translator import Translator

    work.mkdir(parents=True, exist_ok=True)
    profile = Analyzer(game).analyze()
    row: dict = {
        "game": game.name,
        "engine": profile.engine.value,
        "arch": profile.arch,
        "feasible": profile.feasible,
    }
    if not profile.feasible:
        row.update(status="impossible",
                   blockers=[b.reason for b in profile.blockers])
        return row

    planner = Planner()
    strategy = planner.choose(profile)[0].name
    dec = Decompiler(work / "decompiled").decompile(profile) if decompile else None
    translated = Translator(work / "translated").translate(profile, strategy)
    plan = Optimizer().plan(profile, strategy)
    runtime = RuntimePlanner().plan(profile, strategy, plan)
    pkg = ApkPackager(work).package(
        game, translated, plan, app_label=game.name, runtime=runtime
    )
    row.update(
        status=pkg.status,
        strategy=strategy.value,
        runtime=runtime.runtime_class,
        apk=pkg.apk,
        signed=pkg.signed,
        aligned=pkg.aligned,
        abis=pkg.abis,
        decompile=(dec.status if dec else "skipped"),
        tools_used=(dec.tools_used if dec else []),
    )
    return row


def _cmd_portall(args: argparse.Namespace) -> int:
    """One command over every strategy class: build all fixtures end-to-end."""
    import tempfile

    from tests import fixtures_builder

    games = [Path(g) for g in (args.games or [])]
    tmp_ctx = None
    if not games:
        tmp_ctx = tempfile.TemporaryDirectory()
        fx_root = Path(tmp_ctx.name) / "fixtures"
        fixtures = fixtures_builder.build_all(fx_root)
        games = [f.path for f in fixtures]
        expected = {f.path.name: f.expect_feasible for f in fixtures}
    else:
        expected = {g.name: True for g in games}

    work_root = Path(args.work_dir)
    rows = []
    for g in games:
        row = _port_one(g, work_root / g.name, decompile=not args.no_decompile)
        row["expected_feasible"] = expected.get(g.name, True)
        rows.append(row)

    ok = 0
    for r in rows:
        good = (r["status"] == "ok" and r["expected_feasible"]) or (
            r["status"] == "impossible" and not r["expected_feasible"]
        )
        r["verdict"] = "ok" if good else "FAIL"
        ok += good

    summary = {
        "command": "portall",
        "total": len(rows),
        "ok": ok,
        "failed": len(rows) - ok,
        "rows": rows,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if tmp_ctx is not None:
        tmp_ctx.cleanup()
    return 0 if ok == len(rows) else 1


def _cmd_memory(args: argparse.Namespace) -> int:
    from .memory import Memory

    mem = Memory(args.memory)
    rules = [
        {
            "engine": r.engine,
            "graphics_api": r.graphics_api,
            "strategy": r.strategy,
            "weight": r.weight,
            "successes": r.successes,
            "failures": r.failures,
            "confidence": r.confidence,
        }
        for r in mem.all_rules()
    ]
    print(json.dumps({"stats": mem.stats(), "rules": rules}, indent=2))
    mem.close()
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import launch, is_available
    except Exception as exc:  # noqa: BLE001
        print(f"error: GUI unavailable: {exc}", file=sys.stderr)
        return 2
    if not is_available():
        print(
            "error: Tkinter is not available on this host.\n"
            "hint: install it (e.g. 'apt-get install python3-tk') or use the "
            "interactive menu: python run.py",
            file=sys.stderr,
        )
        return 2
    return launch()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="portagent", description="Iterative PC-game -> Android porter")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="reconnaissance only")
    a.add_argument("game", help="game folder or installer file")
    a.set_defaults(func=_cmd_analyze)

    r = sub.add_parser("run", help="run the full ANALYZE..LEARN loop")
    r.add_argument("game", help="game folder or installer file")
    r.add_argument("--work-dir", default="portagent_run")
    r.add_argument("--memory", default=None, help="sqlite memory path")
    r.add_argument("--oracle", default=None, help="JSON of per-scene oracle trace hashes")
    r.add_argument("--max-iterations", type=int, default=6)
    r.set_defaults(func=_cmd_run)

    m = sub.add_parser("memory", help="inspect learned experience")
    m.add_argument("--memory", default="portagent_memory.sqlite")
    m.set_defaults(func=_cmd_memory)

    po = sub.add_parser("port", help="analyze -> translate -> build an installable APK")
    po.add_argument("game", help="game folder or installer file")
    po.add_argument("-o", "--output", default=None, help="output .apk path")
    po.add_argument("--work-dir", default="portagent_out")
    po.add_argument("--no-decompile", action="store_true", help="skip code recovery")
    po.set_defaults(func=_cmd_port)

    pa = sub.add_parser(
        "portall",
        help="one command over every strategy class: build all fixtures -> APKs",
    )
    pa.add_argument("games", nargs="*", help="game folders (default: built-in fixtures)")
    pa.add_argument("--work-dir", default="portagent_all")
    pa.add_argument("--no-decompile", action="store_true", help="skip code recovery")
    pa.set_defaults(func=_cmd_portall)

    gu = sub.add_parser("gui", help="open the desktop GUI (Tkinter window)")
    gu.add_argument("--game", default=None, help="preselect a game folder")
    gu.set_defaults(func=_cmd_gui)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: pass a real path to the game folder or installer, "
            "e.g. analyze \"C:\\\\Games\\\\MyGame\"",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
