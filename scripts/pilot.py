#!/usr/bin/env python3
"""Pilot: run the PortAgent loop over the synthetic game set end to end.

For each feasible fixture an oracle trace is captured (standing in for a trace
from the original game), the loop runs, and the outcome is recorded.  The
anti-cheat fixture must be declared impossible.  A final report is written to
``pilot_report.json``.

Usage:  python scripts/pilot.py [--out DIR] [--memory PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from portagent.analyzer import Analyzer  # noqa: E402
from portagent.models import PortStrategy  # noqa: E402
from portagent.orchestrator import PortAgent  # noqa: E402
from portagent.translator import Translator  # noqa: E402
from portagent.verifier import capture_oracle  # noqa: E402
from tests import fixtures_builder  # noqa: E402


def _oracle(game: Path, work: Path) -> dict[str, str]:
    profile = Analyzer(str(game)).analyze()
    strategy = PortStrategy.UNITY_IL2CPP_NDK if profile.engine.value == "unity" else PortStrategy.NATIVE_DBT_BOX64
    translated = Translator(work / "oracle_tr").translate(profile, strategy)
    return capture_oracle(translated, work / "oracle_run")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="pilot")
    ap.add_argument("--memory", default=None)
    ap.add_argument("--max-iterations", type=int, default=3)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    fx_dir = out / "fixtures"
    built = fixtures_builder.build_all(fx_dir)

    memory_path = args.memory or (out / "memory.sqlite")
    results = []
    for f in built:
        work = out / f.name
        entry = {"fixture": f.name, "engine": f.engine, "expect_feasible": f.expect_feasible}
        if f.expect_feasible:
            oracle = _oracle(f.path, work)
            agent = PortAgent(work, memory_path=memory_path, oracle=oracle, max_iterations=args.max_iterations)
            outcome = agent.run(f.path)
            entry.update(
                {
                    "status": outcome.status,
                    "strategy": outcome.strategy.value if outcome.strategy else None,
                    "iterations": outcome.iterations,
                    "apk": outcome.apk,
                    "verify_verdict": (outcome.verify or {}).get("verdict"),
                    "deterministic": (outcome.verify or {}).get("deterministic"),
                    "equivalence_proven": (outcome.verify or {}).get("equivalence_proven"),
                    "pass": outcome.status == "accepted",
                }
            )
            agent.memory.close()
        else:
            agent = PortAgent(work, memory_path=memory_path, max_iterations=args.max_iterations)
            outcome = agent.run(f.path)
            entry.update(
                {
                    "status": outcome.status,
                    "blockers": [b.to_dict() for b in outcome.profile.blockers],
                    "pass": outcome.status == "impossible",
                }
            )
            agent.memory.close()
        results.append(entry)
        print(f"[pilot] {f.name:20s} -> {entry['status']:12s} pass={entry['pass']}")

    report = {
        "fixtures": len(results),
        "passed": sum(1 for r in results if r["pass"]),
        "results": results,
    }
    (out / "pilot_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[pilot] {report['passed']}/{report['fixtures']} fixtures behaved as expected")
    print(f"[pilot] report: {out / 'pilot_report.json'}")
    return 0 if report["passed"] == report["fixtures"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
