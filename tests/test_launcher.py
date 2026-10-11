"""Tests for the shared launcher API used by run.py and the GUI."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from portagent import launcher
from portagent.packager import find_android_toolchain
from tests import fixtures, have_cross_compilers


class LauncherEnvTests(unittest.TestCase):
    def test_environment_shape(self):
        env = launcher.environment()
        for key in ("python", "platform", "machine", "tools", "android_toolchain"):
            self.assertIn(key, env)
        self.assertIn("objdump", env["tools"])


class LauncherAnalyzeTests(unittest.TestCase):
    def test_analyze_returns_dict(self):
        if not have_cross_compilers():
            raise unittest.SkipTest("cross compilers not installed")
        fx = fixtures()
        prof = launcher.analyze(fx["native_d3d11_x86"])
        self.assertEqual(prof["engine"], "custom_native")
        self.assertIn("feasible", prof)


@unittest.skipUnless(have_cross_compilers(), "cross compilers not installed")
class LauncherPortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not find_android_toolchain():
            raise unittest.SkipTest("Android SDK toolchain not available")
        cls.fx = fixtures()

    def test_run_port_builds_signed_apk(self):
        with tempfile.TemporaryDirectory() as td:
            stages = []
            summary = launcher.run_port(
                self.fx["native_d3d11_x86"], Path(td) / "work",
                progress=lambda s, m: stages.append(s),
            )
            self.assertEqual(summary["status"], "ok")
            self.assertTrue(summary["signed"])
            self.assertTrue(summary["aligned"])
            self.assertTrue(Path(summary["apk"]).exists())
            # Progress spans the whole pipeline.
            for stage in ("ANALYZE", "CHOOSE", "DECOMPILE", "EXECUTE", "VERIFY"):
                self.assertIn(stage, stages)
            self.assertIn("environment", summary)

    def test_report_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            summary = launcher.run_port(self.fx["native_d3d11_x86"], Path(td) / "work")
            report = launcher.build_report(summary, launcher.analyze(self.fx["elf_arm64"]),
                                           include_profile=True)
            md = launcher.format_markdown(report)
            self.assertIn("# PortAgent report", md)
            self.assertIn("Status: **ok**", md)
            self.assertIn("profile", report)


class GuiAvailabilityTests(unittest.TestCase):
    def test_gui_module_imports_and_reports_availability(self):
        from portagent import gui

        self.assertIsInstance(gui.is_available(), bool)

    def test_launch_without_display_is_graceful(self):
        from portagent import gui

        if gui.is_available():
            self.skipTest("Tk display is present; interactive launch not tested here")
        self.assertEqual(gui.launch(), 1)


if __name__ == "__main__":
    unittest.main()
