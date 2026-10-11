import tempfile
import unittest

from tests import fixtures, have_cross_compilers
from portagent.analyzer import Analyzer
from portagent.memory import Memory, profile_signature
from portagent.models import PortStrategy, Verdict


class TestMemory(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers():
            raise unittest.SkipTest("cross compilers not installed")
        cls.fx = fixtures()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = Memory(f"{self.tmp.name}/m.sqlite")

    def tearDown(self):
        self.mem.close()
        self.tmp.cleanup()

    def test_record_and_read_attempt(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        self.mem.record_attempt(p, PortStrategy.UNITY_IL2CPP_NDK, Verdict.PASS, 1)
        self.mem.record_attempt(p, PortStrategy.UNITY_IL2CPP_NDK, Verdict.PASS, 2)
        self.mem.record_attempt(p, PortStrategy.UNITY_MONO_MONOEMBED, Verdict.FAIL, 1)
        attempts = self.mem.attempts_for(p)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(attempts[0]["strategy"], PortStrategy.UNITY_MONO_MONOEMBED.value)

    def test_rule_weighting(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        for _ in range(3):
            self.mem.record_attempt(p, PortStrategy.UNITY_IL2CPP_NDK, Verdict.PASS)
        rule = self.mem.rule_for(p.engine, p.graphics_api)
        self.assertIsNotNone(rule)
        self.assertGreater(rule.confidence, 0.6)

    def test_patch_vectors(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        sig = profile_signature(p)
        self.mem.record_patch(sig, "d3d11->vulkan", "api", {"from": "d3d11", "to": "vulkan"})
        patches = self.mem.patches_for(sig)
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0]["payload"]["to"], "vulkan")

    def test_persistence_across_reopen(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        self.mem.record_attempt(p, PortStrategy.UNITY_IL2CPP_NDK, Verdict.PASS)
        self.mem.close()
        mem2 = Memory(f"{self.tmp.name}/m.sqlite")
        self.assertEqual(mem2.stats()["attempts"], 1)
        mem2.close()

    def test_signature_stable(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        self.assertEqual(profile_signature(p), profile_signature(p))


if __name__ == "__main__":
    unittest.main()
