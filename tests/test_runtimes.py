"""Tests for the ARM64 runtime fetcher and the container launcher generation.

These do not download anything: they exercise the pure helpers (ELF parsing,
``.tzst`` unpacking, readiness checks) and the generated Java, which is where
the real launch chain lives.
"""

import io
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from portagent import runtimes


def _elf(machine: int) -> bytes:
    head = bytearray(64)
    head[0:4] = b"\x7fELF"
    head[4] = 2   # 64-bit
    head[5] = 1   # little-endian
    struct.pack_into("<H", head, 18, machine)
    return bytes(head)


class ElfMachineTests(unittest.TestCase):
    def test_reads_aarch64_machine(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "box64"
            p.write_bytes(_elf(runtimes._ELF_AARCH64))
            self.assertEqual(runtimes._elf_machine(p), runtimes._ELF_AARCH64)

    def test_reads_x86_64_machine(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "wine"
            p.write_bytes(_elf(runtimes._ELF_X86_64))
            self.assertEqual(runtimes._elf_machine(p), runtimes._ELF_X86_64)

    def test_non_elf_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "notes.txt"
            p.write_text("hello")
            self.assertIsNone(runtimes._elf_machine(p))


class WinlatorUrlTests(unittest.TestCase):
    def test_pinned_asset_name_matches_release(self):
        # Regression: the asset is "Winlator_11.2.apk", not "...11.2.0.apk".
        self.assertEqual(runtimes.WINLATOR_ASSET, "Winlator_11.2.apk")
        self.assertTrue(runtimes.WINLATOR_APK_URL.endswith("/Winlator_11.2.apk"))

    def test_resolver_falls_back_to_api_when_pinned_missing(self):
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                import json as _j
                return _j.dumps([
                    {"assets": [{"name": "Winlator_99.0.apk",
                                 "browser_download_url": "https://x/win.apk"}]}
                ]).encode()

        def fake_urlopen(req, timeout=60):
            method = getattr(req, "method", None) or req.get_method()
            if method == "HEAD":
                raise RuntimeError("404")
            return FakeResp()

        with mock.patch.object(runtimes.urllib.request, "urlopen", fake_urlopen):
            url = runtimes._resolve_winlator_url()
        self.assertEqual(url, "https://x/win.apk")


class TzstExtractTests(unittest.TestCase):
    def _make_tzst(self) -> bytes:
        import zstandard

        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w") as tar:
            info = tarfile.TarInfo("usr/local/bin/box64")
            payload = _elf(runtimes._ELF_AARCH64)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        return zstandard.ZstdCompressor().compress(raw.getvalue())

    def test_extract_tzst_roundtrip(self):
        if not runtimes._have_zstd():
            self.skipTest("zstandard not installed")
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "out"
            runtimes._extract_tzst(self._make_tzst(), dest)
            extracted = dest / "usr/local/bin/box64"
            self.assertTrue(extracted.is_file())
            self.assertEqual(runtimes._elf_machine(extracted), runtimes._ELF_AARCH64)


class ReadinessTests(unittest.TestCase):
    def test_empty_home_is_not_ready(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertFalse(runtimes.runtime_ready(Path(td)))
            info = runtimes.info(Path(td))
            self.assertFalse(info["ready"])

    def test_all_binaries_present_is_ready(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            for rel in runtimes._REQUIRED_BINARIES:
                p = home / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(_elf(runtimes._REQUIRED_BINARIES[rel]))
            self.assertTrue(runtimes.runtime_ready(home))


class RuntimePruneTests(unittest.TestCase):
    def test_bundle_prunes_docs_but_keeps_executables(self):
        from portagent.runtime import RuntimeBundler, RuntimeConfig

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "runtime"
            src = home / "wine_dxvk"
            (src / "rootfs/usr/share/man").mkdir(parents=True)
            (src / "rootfs/opt/wine/bin").mkdir(parents=True)
            (src / "rootfs/usr/lib").mkdir(parents=True)
            (src / "rootfs/usr/share/man/wine.1").write_text("doc")
            (src / "rootfs/opt/wine/bin/wine").write_text("ELF")
            (src / "rootfs/usr/lib/libfoo.a").write_text("static")
            (src / "rootfs/usr/lib/libfoo.so").write_text("shared")
            cfg = RuntimeConfig(runtime_class="wine_dxvk", entry="Game.exe")
            dest = Path(td) / "out"
            copied = RuntimeBundler(home).bundle(cfg, dest)

            got = set(copied)
            self.assertIn("rootfs/opt/wine/bin/wine", got)
            self.assertIn("rootfs/usr/lib/libfoo.so", got)
            self.assertNotIn("rootfs/usr/share/man/wine.1", got)
            self.assertNotIn("rootfs/usr/lib/libfoo.a", got)
            self.assertFalse((dest / "rootfs/usr/share/man/wine.1").exists())

    def test_bundle_without_runtime_is_empty(self):
        from portagent.runtime import RuntimeBundler, RuntimeConfig
        with tempfile.TemporaryDirectory() as td:
            cfg = RuntimeConfig(runtime_class="wine_dxvk", entry="Game.exe")
            self.assertEqual(
                RuntimeBundler(Path(td) / "runtime").bundle(cfg, Path(td) / "o"), []
            )


class ContainerActivityTests(unittest.TestCase):
    def test_generated_activity_runs_box64_wine(self):
        from portagent.optimizer import Optimizer
        from portagent.models import Engine, GameProfile, PortStrategy
        from portagent.packager import ApkPackager
        from portagent.runtime import RuntimeConfig

        prof = GameProfile(root="x")
        prof.engine = Engine.GODOT
        plan = Optimizer().plan(prof, PortStrategy.WINE_WINLATOR)
        cfg = RuntimeConfig(runtime_class="wine_dxvk", entry="Game.exe",
                            backend=plan.graphics_backend,
                            env={"PAGENT_ENGINE": "godot"})
        with tempfile.TemporaryDirectory() as td:
            jdir = Path(td) / "java" / "dev" / "portagent" / "port"
            ApkPackager(Path(td))._write_container_activity(
                jdir, "dev.portagent.port", "Game", "godot", "Game.exe", plan, cfg)
            service = (jdir / "PortService.java").read_text()
            activity = (jdir / "PortActivity.java").read_text()
            device = (jdir / "Device.java").read_text()
            probe = (jdir / "GpuProbe.java").read_text()
        # The heavy run lives in a foreground service, not the UI thread.
        self.assertIn("class PortService extends Service", service)
        self.assertIn("startForeground", service)
        self.assertIn("ProcessBuilder", service)
        self.assertIn("box64/usr/local/bin/box64", service)
        self.assertIn("rootfs/opt/wine/bin/wine", service)
        self.assertIn("WINEPREFIX", service)
        self.assertIn("WINEDLLOVERRIDES", service)
        self.assertIn("d3d11", service)
        self.assertIn("prepareWine", service)
        self.assertIn("freedreno_icd.aarch64.json", service)
        self.assertIn("WINELOADERNOEXEC", service)
        self.assertIn("WINEDLLPATH", service)
        self.assertIn('unpack("runtime"', service)
        self.assertIn('unpack("game"', service)
        # Device-aware tuning + display diagnostics.
        self.assertIn("DXVK_FRAME_RATE", service)
        self.assertIn("PAGENT_RENDER_W", service)
        self.assertIn("findXServer", service)
        self.assertIn("Device.detect", service)
        self.assertIn("totalRamMb", device)
        self.assertIn("VIRGL", probe.upper() + device.upper())  # mali->virgl mapping present
        self.assertIn("class PortActivity extends Activity", activity)
        self.assertIn("startForegroundService", activity)


if __name__ == "__main__":
    unittest.main()
