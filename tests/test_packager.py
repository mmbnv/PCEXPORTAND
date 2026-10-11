"""Tests for the Optimizer and the real APK packager.

The packager tests need the Android SDK; when it is absent they are skipped
(never faked) so the suite still passes in a bare container.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from portagent.analyzer import Analyzer
from portagent.models import ApiKind, Engine, GameProfile, PortStrategy
from portagent.optimizer import Optimizer
from portagent.packager import ApkPackager, find_android_toolchain
from portagent.translator import Translator

from tests import fixtures_builder


class OptimizerTests(unittest.TestCase):
    def _profile(self, **kw) -> GameProfile:
        p = GameProfile(root="x")
        for k, v in kw.items():
            setattr(p, k, v)
        return p

    def test_native_engine_prefers_vulkan(self):
        plan = Optimizer().plan(self._profile(engine=Engine.GODOT), PortStrategy.GODOT_ANDROID)
        self.assertEqual(plan.graphics_backend, "vulkan")

    def test_d3d11_needs_dxvk(self):
        plan = Optimizer().plan(
            self._profile(engine=Engine.CUSTOM_NATIVE, graphics_api=ApiKind.D3D11),
            PortStrategy.NATIVE_DBT_BOX64,
        )
        self.assertEqual(plan.graphics_translation, "dxvk")
        self.assertTrue(plan.dbt)

    def test_d3d12_needs_vkd3d(self):
        plan = Optimizer().plan(
            self._profile(engine=Engine.CUSTOM_NATIVE, graphics_api=ApiKind.D3D12),
            PortStrategy.WINE_WINLATOR,
        )
        self.assertEqual(plan.graphics_translation, "vkd3d")
        self.assertGreaterEqual(plan.heap_mb, 1024)

    def test_dbt_env_is_populated(self):
        plan = Optimizer().plan(
            self._profile(engine=Engine.CUSTOM_NATIVE, graphics_api=ApiKind.D3D11),
            PortStrategy.NATIVE_DBT_BOX64,
        )
        env = plan.env()
        self.assertIn("BOX64_DYNAREC_BIGBLOCK", env)
        self.assertEqual(env["PAGENT_AUDIO_RATE"], "48000")


class ToolchainTests(unittest.TestCase):
    def test_find_toolchain_returns_none_or_dict(self):
        saved = os.environ.pop("ANDROID_HOME", None)
        saved2 = os.environ.pop("ANDROID_SDK_ROOT", None)
        try:
            self.assertIsNone(find_android_toolchain())
        finally:
            if saved:
                os.environ["ANDROID_HOME"] = saved
            if saved2:
                os.environ["ANDROID_SDK_ROOT"] = saved2


@unittest.skipUnless(find_android_toolchain() is not None,
                     "Android SDK not installed (set ANDROID_HOME)")
class PackagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dest = Path(cls.tmp.name) / "fixtures"
        cls.fixtures = {f.name: f.path for f in fixtures_builder.build_all(dest)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _package(self, name: str):
        game = self.fixtures[name]
        profile = Analyzer(str(game)).analyze()
        strategy = PortStrategy.GODOT_ANDROID if profile.engine == Engine.GODOT \
            else PortStrategy.NATIVE_DBT_BOX64
        work = Path(self.tmp.name) / ("pkg_" + name)
        translated = Translator(work / "tr").translate(profile, strategy)
        plan = Optimizer().plan(profile, strategy)
        res = ApkPackager(work).package(game, translated, plan, app_label="Test")
        return res

    def test_produces_signed_aligned_apk(self):
        res = self._package("godot_dotnet")
        self.assertEqual(res.status, "ok", msg=str(res.logs[-3:]))
        self.assertTrue(res.signed)
        self.assertTrue(res.aligned)
        self.assertTrue(res.apk and Path(res.apk).is_file())
        self.assertIn("arm64-v8a", res.abis)
        self.assertGreater(res.apk_size, 0)
        self.assertGreaterEqual(res.asset_files, 1)

    def test_apk_contains_expected_entries(self):
        import zipfile

        res = self._package("unity_il2cpp")
        self.assertEqual(res.status, "ok", msg=str(res.logs[-3:]))
        with zipfile.ZipFile(res.apk) as z:
            names = z.namelist()
        self.assertIn("AndroidManifest.xml", names)
        self.assertIn("classes.dex", names)
        self.assertTrue(any(n.startswith("lib/arm64-v8a/") for n in names))
        self.assertTrue(any(n.startswith("assets/game/") for n in names))


if __name__ == "__main__":
    unittest.main()
