import tempfile
import unittest

from tests import fixtures, have_cross_compilers
from portagent.analyzer import Analyzer
from portagent.memory import Memory
from portagent.models import ApiKind, PortStrategy, Verdict
from portagent.planner import Planner


class TestPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers():
            raise unittest.SkipTest("cross compilers not installed")
        cls.fx = fixtures()

    def test_il2cpp_prefers_il2cpp_strategy(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        best = Planner().choose(p)[0]
        self.assertEqual(best.name, PortStrategy.UNITY_IL2CPP_NDK)
        self.assertIn("Il2CppDumper", best.required_tools)

    def test_mono_prefers_monoembed(self):
        p = Analyzer(self.fx["unity_mono"]).analyze()
        best = Planner().choose(p)[0]
        self.assertEqual(best.name, PortStrategy.UNITY_MONO_MONOEMBED)

    def test_anticheat_is_unsupported(self):
        p = Analyzer(self.fx["anticheat_title"]).analyze()
        strategies = Planner().choose(p)
        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].name, PortStrategy.UNSUPPORTED)

    def test_d3d_native_gets_wine_or_dbt(self):
        p = Analyzer(self.fx["native_d3d11_x86"]).analyze()
        names = {s.name for s in Planner().choose(p)}
        self.assertTrue({PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR} & names)

    def test_memory_boost_reorders(self):
        p = Analyzer(self.fx["native_d3d11_x86"]).analyze()
        with tempfile.TemporaryDirectory() as td:
            mem = Memory(f"{td}/m.sqlite")
            # Teach memory that Wine worked for this class.
            for _ in range(4):
                mem.record_attempt(p, PortStrategy.WINE_WINLATOR, Verdict.PASS)
            base = Planner().choose(p)[0].name
            boosted = Planner(mem).choose(p)[0].name
            mem.close()
        self.assertNotEqual(base, boosted)
        self.assertEqual(boosted, PortStrategy.WINE_WINLATOR)


if __name__ == "__main__":
    unittest.main()
