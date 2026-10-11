"""Tests for the Decompiler and the runtime layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portagent.analyzer import Analyzer
from portagent.decompiler import Decompiler, find_tools, summarize
from portagent.models import ApiKind, Engine, GameProfile, PortStrategy
from portagent.optimizer import Optimizer
from portagent.runtime import RuntimeBundler, RuntimeConfig, RuntimePlanner, write_config

from tests import fixtures_builder

_HAS_MONODIS = "monodis" in find_tools()


class DecompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dest = Path(cls.tmp.name) / "fixtures"
        cls.fixtures = {f.name: f.path for f in fixtures_builder.build_all(dest)}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_find_tools_reports_installed(self):
        tools = find_tools()
        self.assertIsInstance(tools, dict)
        # objdump/readelf ship with binutils which the fixtures need anyway.
        self.assertIn("objdump", tools)

    def test_summarize_counts(self):
        prof = Analyzer(str(self.fixtures["unity_mono"])).analyze()
        inv = summarize(prof)
        self.assertGreaterEqual(inv["binaries"], 2)
        self.assertGreater(inv["total_bytes"], 0)

    @unittest.skipUnless(_HAS_MONODIS, "monodis not installed")
    def test_recovers_managed_il(self):
        prof = Analyzer(str(self.fixtures["unity_mono"])).analyze()
        res = Decompiler(Path(self.tmp.name) / "dec_mono").decompile(prof)
        self.assertIn("monodis", res.tools_used)
        self.assertIn("managed_il", res.outputs)
        self.assertGreaterEqual(res.recovered.get("types", 0), 2)
        self.assertGreaterEqual(res.recovered.get("methods", 0), 2)
        il = Path(res.outputs["managed_il"]).read_text(encoding="utf-8")
        self.assertIn(".class", il)

    def test_disassembles_native(self):
        prof = Analyzer(str(self.fixtures["native_d3d11_x86"])).analyze()
        res = Decompiler(Path(self.tmp.name) / "dec_native").decompile(prof)
        self.assertIn("objdump", res.tools_used)
        self.assertIn("native_asm", res.outputs)
        self.assertGreater(res.recovered.get("asm_lines", 0), 100)


class RuntimePlannerTests(unittest.TestCase):
    def _plan(self, engine=Engine.UNITY, api=ApiKind.OPENGL, strategy=PortStrategy.UNITY_MONO_MONOEMBED,
              managed=False):
        prof = GameProfile(root="x")
        prof.engine = engine
        prof.graphics_api = api
        prof.managed = managed
        opt = Optimizer().plan(prof, strategy)
        return RuntimePlanner().plan(prof, strategy, opt)

    def test_engine_title_uses_engine_runtime(self):
        cfg = self._plan(engine=Engine.GODOT, strategy=PortStrategy.GODOT_ANDROID)
        self.assertEqual(cfg.runtime_class, "engine_native")

    def test_native_dbt_title(self):
        cfg = self._plan(engine=Engine.CUSTOM_NATIVE, strategy=PortStrategy.NATIVE_DBT_BOX64)
        self.assertEqual(cfg.runtime_class, "dbt_box64")

    def test_wine_title(self):
        cfg = self._plan(engine=Engine.CUSTOM_NATIVE, api=ApiKind.D3D11,
                         strategy=PortStrategy.WINE_WINLATOR)
        self.assertEqual(cfg.runtime_class, "wine_dxvk")
        self.assertEqual(cfg.translation, "dxvk")

    def test_managed_title(self):
        cfg = self._plan(engine=Engine.UNKNOWN, managed=True,
                         strategy=PortStrategy.DOTNET_MONO_EMBED)
        self.assertEqual(cfg.runtime_class, "mono_managed")

    def test_env_carries_engine_and_strategy(self):
        cfg = self._plan(engine=Engine.GODOT, strategy=PortStrategy.GODOT_ANDROID)
        self.assertEqual(cfg.env["PAGENT_ENGINE"], "godot")
        self.assertEqual(cfg.env["PAGENT_STRATEGY"], "godot_android")

    def test_d3d_native_gets_translation(self):
        cfg = self._plan(engine=Engine.CUSTOM_NATIVE, api=ApiKind.D3D12,
                         strategy=PortStrategy.NATIVE_DBT_BOX64)
        self.assertEqual(cfg.translation, "vkd3d")


class RuntimeBundlerTests(unittest.TestCase):
    def test_bundler_copies_installed_payload(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "runtime"
            (home / "dbt_box64").mkdir(parents=True)
            (home / "dbt_box64" / "libbox64.so").write_bytes(b"\x7fELF" + b"\x00" * 16)
            (home / "dbt_box64" / "sub").mkdir()
            (home / "dbt_box64" / "sub" / "box64").write_bytes(b"\x7fELF")
            cfg = RuntimeConfig(runtime_class="dbt_box64")
            dest = Path(td) / "out"
            copied = RuntimeBundler(home).bundle(cfg, dest)
            self.assertEqual(len(copied), 2)
            self.assertTrue((dest / "libbox64.so").is_file())
            self.assertTrue((dest / "sub" / "box64").is_file())

    def test_bundler_empty_when_not_installed(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = RuntimeConfig(runtime_class="wine_dxvk")
            copied = RuntimeBundler(Path(td) / "nope").bundle(cfg, Path(td) / "out")
            self.assertEqual(copied, [])

    def test_write_config_roundtrip(self):
        import json

        with tempfile.TemporaryDirectory() as td:
            cfg = RuntimeConfig(runtime_class="mono_managed", entry="Game.dll",
                                env={"PAGENT_ENGINE": "unity"})
            p = write_config(cfg, Path(td))
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["runtime_class"], "mono_managed")
            self.assertEqual(data["entry"], "Game.dll")
            self.assertEqual(data["env"]["PAGENT_ENGINE"], "unity")


if __name__ == "__main__":
    unittest.main()
