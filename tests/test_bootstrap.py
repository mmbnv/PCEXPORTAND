"""Tests for the pure-Python toolchain bootstrap and Windows-safe invocation.

Nothing here downloads anything: the network is stubbed by asserting on the URL
builders and the idempotent "already present" paths. The Windows ``.bat``
handling is checked by inspecting the argv the packager builds.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portagent import bootstrap as bs


class UrlTests(unittest.TestCase):
    def test_jdk_url_targets_adoptium_for_this_os(self) -> None:
        url = bs._jdk_url()
        self.assertIn("api.adoptium.net", url)
        self.assertIn(f"/{bs.JDK_FEATURE}/ga/", url)
        self.assertTrue(url.endswith("/jdk/hotspot/normal/eclipse"))

    def test_cmdline_tools_url_is_pinned_and_per_os(self) -> None:
        url = bs._cmdline_tools_url()
        self.assertIn("dl.google.com", url)
        self.assertIn(bs.CMDLINE_TOOLS_VERSION, url)
        self.assertTrue(url.endswith("_latest.zip"))


class FindJavaHomeTests(unittest.TestCase):
    def test_finds_javac_in_unpacked_tree(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "jdk"
            bin_dir = home / "jdk-17.0.11+9" / "bin"
            bin_dir.mkdir(parents=True)
            exe = "javac.exe" if os.name == "nt" else "javac"
            (bin_dir / exe).write_text("")
            found = bs._find_java_home(home)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "jdk-17.0.11+9")

    def test_returns_none_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(bs._find_java_home(Path(td) / "jdk"))


class ValidJavaHomeTests(unittest.TestCase):
    def _exe(self) -> str:
        return "javac.exe" if os.name == "nt" else "javac"

    def test_true_only_when_bin_javac_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "jdk-17"
            (good / "bin").mkdir(parents=True)
            (good / "bin" / self._exe()).write_text("")
            self.assertTrue(bs._is_valid_java_home(good))

    def test_false_for_windows_shim_dir(self) -> None:
        # The Oracle javapath shim dir has javac.exe but no bin/ subdir, so it
        # is not a JDK home (this is what broke sdkmanager on Windows).
        with tempfile.TemporaryDirectory() as td:
            shim = Path(td) / "Java" / "javapath"
            shim.mkdir(parents=True)
            (shim / self._exe()).write_text("")
            self.assertFalse(bs._is_valid_java_home(shim.parent))

    def test_false_for_missing_or_empty(self) -> None:
        self.assertFalse(bs._is_valid_java_home(None))
        self.assertFalse(bs._is_valid_java_home(""))
        self.assertFalse(bs._is_valid_java_home("/no/such/dir"))


class EnsureJdkTests(unittest.TestCase):
    def test_reuses_already_unpacked_jdk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bin_dir = home / "jdk" / "jdk-17" / "bin"
            bin_dir.mkdir(parents=True)
            exe = "javac.exe" if os.name == "nt" else "javac"
            (bin_dir / exe).write_text("")
            with mock.patch.object(bs, "_download") as dl:
                jdk = bs.ensure_jdk(home)
            dl.assert_not_called()
            self.assertIsNotNone(jdk)

    def test_ignores_bogus_java_home_and_downloads(self) -> None:
        # Regression: JAVA_HOME pointing at .../Common Files/Oracle/Java and a
        # shim javac on PATH must NOT be accepted; a real JDK is downloaded.
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            exe = "javac.exe" if os.name == "nt" else "javac"
            shim = home / "Oracle" / "Java" / "javapath"
            shim.mkdir(parents=True)
            (shim / exe).write_text("")

            def fake_download(url, dest, progress=None, label=""):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"")
                unpacked = home / "jdk" / "jdk-17.0.11+9" / "bin"
                unpacked.mkdir(parents=True)
                (unpacked / exe).write_text("")

            with mock.patch.dict(os.environ, {"JAVA_HOME": str(shim.parent)}), \
                    mock.patch.object(bs.shutil, "which", return_value=str(shim / exe)), \
                    mock.patch.object(bs, "_download", fake_download), \
                    mock.patch.object(bs, "_unzip", lambda *a, **k: None):
                jdk = bs.ensure_jdk(home)
            self.assertIsNotNone(jdk)
            self.assertTrue(bs._is_valid_java_home(jdk))
            self.assertNotEqual(str(jdk), str(shim.parent))

    def test_prefers_valid_java_home_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            real = home / "system-jdk"
            (real / "bin").mkdir(parents=True)
            exe = "javac.exe" if os.name == "nt" else "javac"
            (real / "bin" / exe).write_text("")
            with mock.patch.dict(os.environ, {"JAVA_HOME": str(real)}), \
                    mock.patch.object(bs, "_download") as dl:
                jdk = bs.ensure_jdk(home)
            dl.assert_not_called()
            self.assertEqual(Path(jdk), real)


class FindNdkTests(unittest.TestCase):
    def test_picks_newest_ndk_with_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sdk = Path(td)
            for ver in ("25.0.0", "26.1.10909125"):
                pre = sdk / "ndk" / ver / "toolchains" / "llvm" / "prebuilt"
                pre.mkdir(parents=True)
            found = bs._find_ndk(sdk)
            self.assertEqual(found.name, "26.1.10909125")

    def test_none_without_ndk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(bs._find_ndk(Path(td)))


class ApplyEnvTests(unittest.TestCase):
    def test_apply_env_exports_values(self) -> None:
        res = bs.BootstrapResult(
            env={"ANDROID_HOME": "/tmp/sdk", "JAVA_HOME": "/tmp/jdk"},
            java_home="/tmp/jdk",
        )
        saved = {k: os.environ.get(k) for k in res.env}
        try:
            bs.apply_env(res)
            self.assertEqual(os.environ["ANDROID_HOME"], "/tmp/sdk")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_bootstrap_persists_env_json(self) -> None:
        # Idempotent path: pre-seed jdk + sdk so bootstrap does no network I/O.
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            bin_dir = home / "jdk" / "jdk-17" / "bin"
            bin_dir.mkdir(parents=True)
            exe = "javac.exe" if os.name == "nt" else "javac"
            (bin_dir / exe).write_text("")
            ndk = home / "android-sdk" / "ndk" / "26.1.10909125"
            (ndk / "toolchains" / "llvm" / "prebuilt").mkdir(parents=True)
            with mock.patch.object(bs, "ensure_android_sdk",
                                   return_value=home / "android-sdk"):
                res = bs.bootstrap(home=home)
            self.assertTrue((home / "env.json").is_file())
            self.assertTrue(res.ok)


class AutoLoadTests(unittest.TestCase):
    def test_env_json_is_loaded_without_shell_vars(self) -> None:
        import json

        from portagent import toolpaths

        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "env.json").write_text(
                json.dumps({"ANDROID_HOME": "/opt/sdk", "JAVA_HOME": "/opt/jdk"}),
                encoding="utf-8",
            )
            clean = {k: v for k, v in os.environ.items()
                     if not k.startswith(("ANDROID_", "JAVA_HOME", "PAGENT_"))}
            clean["PAGENT_TOOLS_HOME"] = td
            with mock.patch.dict(os.environ, clean, clear=True):
                self.assertEqual(toolpaths.android_home(), "/opt/sdk")
                self.assertEqual(os.environ.get("JAVA_HOME"), "/opt/jdk")


class WindowsInvocationTests(unittest.TestCase):
    def test_run_wraps_bat_through_cmd_on_windows(self) -> None:
        from portagent import packager

        captured: dict = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv

            class P:
                returncode = 0
                stdout = ""
                stderr = ""

            return P()

        with mock.patch.object(packager.os, "name", "nt"), \
                mock.patch.object(packager.subprocess, "run", fake_run):
            rc, _ = packager._run([r"C:\sdk\sdkmanager.bat", "install", "x"])
        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"][:2], ["cmd", "/c"])
        self.assertTrue(captured["argv"][2].endswith("sdkmanager.bat"))

    def test_run_does_not_wrap_plain_binary(self) -> None:
        from portagent import packager

        captured: dict = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv

            class P:
                returncode = 0
                stdout = ""
                stderr = ""

            return P()

        with mock.patch.object(packager.os, "name", "nt"), \
                mock.patch.object(packager.subprocess, "run", fake_run):
            packager._run(["aapt2.exe", "version"])
        self.assertEqual(captured["argv"], ["aapt2.exe", "version"])

    def test_java_tool_prefers_java_home(self) -> None:
        from portagent import packager

        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            exe = "javac.exe" if os.name == "nt" else "javac"
            (bin_dir / exe).write_text("")
            with mock.patch.dict(os.environ, {"JAVA_HOME": td}):
                resolved = packager._java_tool("javac")
            self.assertEqual(Path(resolved), bin_dir / exe)


if __name__ == "__main__":
    unittest.main()
