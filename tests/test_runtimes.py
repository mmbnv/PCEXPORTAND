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
            src = (jdir / "PortActivity.java").read_text()
        self.assertIn("class PortActivity extends Activity", src)
        self.assertIn("ProcessBuilder", src)
        self.assertIn("box64/usr/local/bin/box64", src)
        self.assertIn("rootfs/opt/wine/bin/wine", src)
        self.assertIn("WINEPREFIX", src)
        self.assertIn("WINEDLLOVERRIDES", src)
        self.assertIn("d3d11", src)
        self.assertIn("prepareWine", src)
        self.assertIn("freedreno_icd.aarch64.json", src)
        self.assertIn("WINELOADERNOEXEC", src)
        self.assertIn("WINEDLLPATH", src)
        self.assertIn('unpackAssets("runtime"', src)
        self.assertIn('unpackAssets("game"', src)


if __name__ == "__main__":
    unittest.main()
