import tempfile
import unittest
from pathlib import Path

from tests import fixtures, have_cc, have_cross_compilers
from portagent.analyzer import Analyzer
from portagent.models import PortStrategy, StepName
from portagent.orchestrator import PortAgent
from portagent.translator import Translator
from portagent.verifier import capture_oracle


def _oracle_for(path: Path, work: Path) -> dict:
    profile = Analyzer(str(path)).analyze()
    translated = Translator(work / "oracle_tr").translate(profile, PortStrategy.NATIVE_DBT_BOX64)
    return capture_oracle(translated, work / "oracle_run")


class TestOrchestrator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers() or not have_cc():
            raise unittest.SkipTest("cross compilers / host cc not installed")
        cls.fx = fixtures()
        cls.tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_loop_accepts_with_oracle(self):
        work = Path(self.tmp.name) / "run_accept"
        oracle = _oracle_for(self.fx["native_d3d11_x86"], work)
        agent = PortAgent(work, memory_path=work / "m.sqlite", oracle=oracle, max_iterations=3)
        outcome = agent.run(self.fx["native_d3d11_x86"])
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.verify["verdict"], "pass")
        steps = [r["step"] for r in outcome.reports]
        for expected in ("ANALYZE", "CHOOSE", "EXECUTE", "VERIFY", "LEARN", "ACCEPT"):
            self.assertIn(expected, steps)
        # Every report obeys the mission JSON contract.
        for r in outcome.reports:
            for key in ("step", "input", "action", "artifacts", "result", "next", "memory_update"):
                self.assertIn(key, r)

    def test_impossible_short_circuits(self):
        work = Path(self.tmp.name) / "run_impossible"
        agent = PortAgent(work, memory_path=work / "m.sqlite", max_iterations=3)
        outcome = agent.run(self.fx["anticheat_title"])
        self.assertEqual(outcome.status, "impossible")
        self.assertEqual(outcome.iterations, 1)
        steps = [r["step"] for r in outcome.reports]
        self.assertIn(StepName.IMPOSSIBLE.value, steps)
        self.assertNotIn("EXECUTE", steps)

    def test_wrong_oracle_never_accepts(self):
        work = Path(self.tmp.name) / "run_fail"
        bad = {"menu": "aa", "gameplay": "bb", "save": "cc"}
        agent = PortAgent(work, memory_path=work / "m.sqlite", oracle=bad, max_iterations=2)
        outcome = agent.run(self.fx["native_d3d11_x86"])
        self.assertNotEqual(outcome.status, "accepted")
        self.assertIn(outcome.status, ("inconclusive", "exhausted"))
        # The loop must have learned from the failures.
        self.assertGreaterEqual(agent.memory.stats()["attempts"], 1)

    def test_memory_accumulates_and_boosts(self):
        work = Path(self.tmp.name) / "run_mem"
        oracle = _oracle_for(self.fx["unity_il2cpp"], work)
        agent = PortAgent(work, memory_path=work / "m.sqlite", oracle=oracle, max_iterations=2)
        agent.run(self.fx["unity_il2cpp"])
        rules = agent.memory.all_rules()
        self.assertTrue(any(r.engine == "unity" and r.successes > 0 for r in rules))

    def test_missing_sdk_stops_early_with_clear_message(self):
        import os

        work = Path(self.tmp.name) / "run_no_sdk"
        saved = {k: os.environ.pop(k, None) for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT")}
        saved_home = os.environ.get("PAGENT_TOOLS_HOME")
        empty_tools = Path(self.tmp.name) / "empty_tools"
        empty_tools.mkdir()
        os.environ["PAGENT_TOOLS_HOME"] = str(empty_tools)
        try:
            agent = PortAgent(work, memory_path=work / "m.sqlite", max_iterations=6)
            outcome = agent.run(self.fx["native_d3d11_x86"])
        finally:
            if saved_home is None:
                os.environ.pop("PAGENT_TOOLS_HOME", None)
            else:
                os.environ["PAGENT_TOOLS_HOME"] = saved_home
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        # A missing SDK blocks every strategy, so the loop must not burn its budget.
        self.assertEqual(outcome.status, "exhausted")
        self.assertEqual(outcome.iterations, 1)
        self.assertIsNotNone(outcome.strategy)
        self.assertIn("SDK", outcome.message)
        # The strategy actually chosen is reported, not dropped.
        self.assertIn(outcome.strategy.value, " ".join(r["action"] for r in outcome.reports))


if __name__ == "__main__":
    unittest.main()
