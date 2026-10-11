import json
import tempfile
import unittest
from pathlib import Path

from tests import fixtures, have_cc, have_cross_compilers
from portagent.analyzer import Analyzer
from portagent.builder import Builder
from portagent.models import PortStrategy
from portagent.translator import Translator
from portagent.verifier import Verifier, capture_oracle


class TestTranslateBuildVerify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers() or not have_cc():
            raise unittest.SkipTest("cross compilers / host cc not installed")
        cls.fx = fixtures()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.profile = Analyzer(cls.fx["native_d3d11_x86"]).analyze()
        cls.translated = Translator(Path(cls.tmp.name) / "tr").translate(
            cls.profile, PortStrategy.NATIVE_DBT_BOX64
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_mapping_json_has_expected_targets(self):
        m = self.translated["mapping"]
        self.assertEqual(m["graphics"]["target"], "vulkan")
        self.assertIn("DXVK", m["graphics"]["layer"])
        self.assertEqual(m["audio"]["target"], "aaudio")
        self.assertIn("CreateFileA", m["win32"])

    def test_generated_c_compiles(self):
        b = Builder(Path(self.tmp.name) / "b")
        objects, logs, commands = b.compile_native(self.translated, abi="host")
        self.assertEqual(len(objects), 3, msg="\n".join(logs))
        self.assertTrue(all(Path(o).exists() for o in objects))

    def test_build_scaffolds_android_project(self):
        b = Builder(Path(self.tmp.name) / "b2")
        result = b.build(self.profile, PortStrategy.NATIVE_DBT_BOX64, self.translated)
        proj = Path(result.project_dir)
        self.assertTrue((proj / "app" / "build.gradle").exists())
        self.assertTrue((proj / "app" / "src" / "main" / "AndroidManifest.xml").exists())
        self.assertTrue((proj / "app" / "src" / "main" / "cpp" / "CMakeLists.txt").exists())
        # Host compilation should succeed even without the Android SDK.
        self.assertIn(result.status, ("ok", "partial"))
        self.assertIn("host", result.abis)

    def test_determinism_and_scene_harness(self):
        b = Builder(Path(self.tmp.name) / "b3")
        objects, logs, _ = b.compile_native(self.translated, abi="host")
        oracle = capture_oracle(self.translated, Path(self.tmp.name) / "oracle")
        v = Verifier(Path(self.tmp.name) / "v", oracle=oracle).verify(self.translated, objects, "partial")
        self.assertTrue(v.deterministic)
        self.assertEqual(set(v.scene_hashes), {"menu", "gameplay", "save"})
        self.assertTrue(v.equivalence_proven)
        self.assertEqual(v.verdict.value, "pass")
        self.assertTrue(v.checks["scenes_pass"])

    def test_oracle_mismatch_fails(self):
        b = Builder(Path(self.tmp.name) / "b4")
        objects, _, _ = b.compile_native(self.translated, abi="host")
        bad_oracle = {"menu": "deadbeef", "gameplay": "cafef00d", "save": "0badf00d"}
        v = Verifier(Path(self.tmp.name) / "v2", oracle=bad_oracle).verify(self.translated, objects, "partial")
        self.assertEqual(v.verdict.value, "fail")
        self.assertTrue(v.mismatches)

    def test_no_oracle_is_unknown_not_pass(self):
        b = Builder(Path(self.tmp.name) / "b5")
        objects, _, _ = b.compile_native(self.translated, abi="host")
        v = Verifier(Path(self.tmp.name) / "v3", oracle={}).verify(self.translated, objects, "partial")
        self.assertEqual(v.verdict.value, "unknown")
        self.assertFalse(v.equivalence_proven)


if __name__ == "__main__":
    unittest.main()
