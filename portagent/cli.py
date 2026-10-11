"""Command-line entry point: ``python -m portagent ...``."""

from __future__ import annotations

import argparse
import json
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
    print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
    return {"accepted": 0, "impossible": 3, "exhausted": 4, "inconclusive": 5}.get(outcome.status, 1)


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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
