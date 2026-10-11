import unittest

from tests import fixtures, have_cross_compilers
from portagent.analyzer import Analyzer
from portagent.models import ApiKind, Engine


class TestAnalyzer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers():
            raise unittest.SkipTest("cross compilers not installed")
        cls.fx = fixtures()

    def test_unity_il2cpp(self):
        p = Analyzer(self.fx["unity_il2cpp"]).analyze()
        self.assertEqual(p.engine, Engine.UNITY)
        self.assertTrue(p.il2cpp)
        self.assertFalse(p.managed)
        self.assertEqual(p.graphics_api, ApiKind.D3D11)
        self.assertEqual(p.audio_api, ApiKind.XAUDIO)
        self.assertEqual(p.arch, "x86_64")
        self.assertTrue(p.feasible)

    def test_unity_mono(self):
        p = Analyzer(self.fx["unity_mono"]).analyze()
        self.assertEqual(p.engine, Engine.UNITY)
        self.assertTrue(p.managed)
        self.assertFalse(p.il2cpp)
        self.assertTrue(p.feasible)

    def test_native_d3d11_x86(self):
        p = Analyzer(self.fx["native_d3d11_x86"]).analyze()
        self.assertEqual(p.arch, "x86")
        self.assertEqual(p.graphics_api, ApiKind.D3D11)
        self.assertEqual(p.audio_api, ApiKind.XAUDIO)
        self.assertIn(ApiKind.WIN32, p.runtime_apis)
        # Steam DRM wrapper is recorded but is not a hard blocker.
        self.assertTrue(any("steam" in d.lower() for d in p.drm))
        self.assertTrue(p.feasible)

    def test_rpgmaker(self):
        p = Analyzer(self.fx["rpgmaker_mv"]).analyze()
        self.assertEqual(p.engine, Engine.RPGMAKER)
        self.assertTrue(p.feasible)

    def test_godot(self):
        p = Analyzer(self.fx["godot"]).analyze()
        self.assertEqual(p.engine, Engine.GODOT)
        self.assertEqual(p.graphics_api, ApiKind.OPENGL)

    def test_godot_dotnet(self):
        p = Analyzer(self.fx["godot_dotnet"]).analyze()
        self.assertEqual(p.engine, Engine.GODOT)
        self.assertGreaterEqual(p.engine_confidence, 0.5)
        self.assertTrue(p.managed)  # Godot .NET ships managed assemblies
        # Steam is user-space, not a hard blocker.
        self.assertTrue(any("steam" in d.lower() for d in p.drm))
        self.assertTrue(p.feasible)
        # The misleading "needs DBT/Wine" note must NOT appear for a known engine.
        self.assertFalse(any("DBT" in n for n in p.notes))

    def test_managed_detection_via_clr_header(self):
        from portagent.analyzer import _has_clr_header

        dll = self.fx["unity_mono"] / "MonoGame_Data" / "Managed" / "Assembly-CSharp.dll"
        self.assertTrue(_has_clr_header(dll.read_bytes()[:0x4000]))
        native = self.fx["unity_mono"] / "UnityPlayer.dll"
        self.assertFalse(_has_clr_header(native.read_bytes()[:0x4000]))
        exe = self.fx["native_d3d11_x86"] / "IndieGame.exe"
        self.assertFalse(_has_clr_header(exe.read_bytes()[:0x4000]))

    def test_anticheat_is_impossible(self):
        p = Analyzer(self.fx["anticheat_title"]).analyze()
        self.assertFalse(p.feasible)
        self.assertTrue(any(b.severity == "hard" for b in p.blockers))
        self.assertTrue(any("anti-cheat" in b.reason.lower() or "kernel" in b.reason.lower()
                            for b in p.blockers))

    def test_elf_arm64_arch(self):
        p = Analyzer(self.fx["elf_arm64"]).analyze()
        self.assertEqual(p.arch, "arm64")
        self.assertTrue(p.feasible)

    def test_installer_file_input(self):
        # A single PE file is profiled as an installer, not a folder.
        p = Analyzer(self.fx["native_d3d11_x86"] / "IndieGame.exe").analyze()
        self.assertEqual(p.file_count, 1)
        self.assertTrue(p.notes)


if __name__ == "__main__":
    unittest.main()
