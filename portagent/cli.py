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
        oracle = json.loads(Path(args.oracle).read_text(encoding="utf-8"))
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
    from .optimizer import Optimizer
    from .packager import ApkPackager
    from .planner import Planner
    from .translator import Translator

    game = Path(args.game)
    if not game.exists():
        raise FileNotFoundError(f"no such file or directory: {game}")
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)

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

    translated = Translator(work / "translated").translate(profile, strategy)
    plan = Optimizer().plan(profile, strategy)
    result = ApkPackager(work).package(game, translated, plan, app_label=label)

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
    po.set_defaults(func=_cmd_port)
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
