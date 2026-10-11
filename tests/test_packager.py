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
from portagent.runtime import RuntimePlanner
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
        # An explicit, empty tools home + no ANDROID_HOME must yield None, even
        # though the machine may have a real SDK cached elsewhere.
        saved = {k: os.environ.pop(k, None) for k in ("ANDROID_HOME", "ANDROID_SDK_ROOT")}
        saved_home = os.environ.get("PAGENT_TOOLS_HOME")
        with tempfile.TemporaryDirectory() as empty:
            os.environ["PAGENT_TOOLS_HOME"] = empty
            try:
                self.assertIsNone(find_android_toolchain())
            finally:
                if saved_home is None:
                    os.environ.pop("PAGENT_TOOLS_HOME", None)
                else:
                    os.environ["PAGENT_TOOLS_HOME"] = saved_home
                for k, v in saved.items():
                    if v is not None:
                        os.environ[k] = v

    def test_windows_style_tool_names_are_found(self):
        # Windows build-tools are aapt2.exe / d8.bat, not bare names.
        with tempfile.TemporaryDirectory() as tmp:
            sdk = Path(tmp)
            bt = sdk / "build-tools" / "34.0.0"
            (sdk / "platforms" / "android-34").mkdir(parents=True)
            (sdk / "platforms" / "android-34" / "android.jar").write_bytes(b"PK\x03\x04")
            bt.mkdir(parents=True)
            (bt / "aapt2.exe").write_bytes(b"")
            (bt / "d8.bat").write_bytes(b"")
            (bt / "zipalign.exe").write_bytes(b"")
            (bt / "apksigner.bat").write_bytes(b"")
            saved = os.environ.get("ANDROID_HOME")
            os.environ["ANDROID_HOME"] = str(sdk)
            os.environ.pop("ANDROID_SDK_ROOT", None)
            try:
                tools = find_android_toolchain()
            finally:
                if saved is None:
                    os.environ.pop("ANDROID_HOME", None)
                else:
                    os.environ["ANDROID_HOME"] = saved
            self.assertIsNotNone(tools)
            self.assertTrue(tools["aapt2"].endswith("aapt2.exe"))
            self.assertTrue(tools["d8"].endswith("d8.bat"))
            self.assertTrue(tools["zipalign"].endswith("zipalign.exe"))
            self.assertTrue(tools["apksigner"].endswith("apksigner.bat"))


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
        runtime = RuntimePlanner().plan(profile, strategy, plan)
        res = ApkPackager(work).package(game, translated, plan, app_label="Test",
                                       runtime=runtime)
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

    def test_apk_embeds_runtime_config(self):
        import json
        import zipfile

        res = self._package("native_d3d11_x86")
        self.assertEqual(res.status, "ok", msg=str(res.logs[-3:]))
        self.assertEqual(res.runtime_class, "dbt_box64")
        with zipfile.ZipFile(res.apk) as z:
            cfg = json.loads(z.read("assets/game/portagent_runtime/env.json"))
        self.assertEqual(cfg["runtime_class"], "dbt_box64")
        self.assertEqual(cfg["entry"], "IndieGame.exe")
        self.assertEqual(cfg["env"]["PAGENT_STRATEGY"], "native_dbt_box64")


@unittest.skipUnless(find_android_toolchain() is not None,
                     "Android SDK not installed (set ANDROID_HOME)")
class UnifiedPipelineTests(unittest.TestCase):
    """The single command must cover every strategy class end to end."""

    @classmethod
    def setUpClass(cls):
        from portagent.cli import _port_one

        cls._port_one = staticmethod(_port_one)
        cls.tmp = tempfile.TemporaryDirectory()
        dest = Path(cls.tmp.name) / "fixtures"
        cls.fixtures = {f.name: f.path for f in fixtures_builder.build_all(dest)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_class_builds_or_is_impossible(self):
        for name, path in self.fixtures.items():
            with self.subTest(game=name):
                work = Path(self.tmp.name) / ("one_" + name)
                row = self._port_one(path, work)
                if row["feasible"]:
                    self.assertEqual(row["status"], "ok", msg=str(row))
                    self.assertTrue(row["signed"])
                    self.assertTrue(Path(row["apk"]).is_file())
                    self.assertTrue(row["runtime"])
                else:
                    self.assertEqual(row["status"], "impossible")


if __name__ == "__main__":
    unittest.main()
