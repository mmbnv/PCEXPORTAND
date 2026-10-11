import unittest

from tests import fixtures, have_cc, have_cross_compilers
from portagent.parsers import parse_elf, parse_file, parse_pe


class TestParsers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have_cross_compilers():
            raise unittest.SkipTest("mingw-w64 / aarch64 cross compilers not installed")
        cls.fx = fixtures()

    def test_pe_arch_and_imports_x86(self):
        p = parse_file(self.fx["native_d3d11_x86"] / "IndieGame.exe")
        self.assertEqual(p.kind, "pe")
        self.assertEqual(p.arch, "x86")
        self.assertEqual(p.bitness, 32)
        self.assertIn("d3d11.dll", p.imports)
        self.assertIn("dxgi.dll", p.imports)
        self.assertIn("xaudio2_9.dll", p.imports)
        self.assertIn("ws2_32.dll", p.imports)
        self.assertIn("kernel32.dll", p.imports)

    def test_pe_arch_x86_64(self):
        p = parse_file(self.fx["unity_il2cpp"] / "GameName.exe")
        self.assertEqual(p.arch, "x86_64")
        self.assertEqual(p.bitness, 64)

    def test_pe_imported_symbols_present(self):
        p = parse_file(self.fx["native_d3d11_x86"] / "IndieGame.exe")
        self.assertTrue(any("Create" in s or "Get" in s for s in p.imported_symbols))

    def test_elf_arm64(self):
        so = self.fx["elf_arm64"] / "lib" / "libengine.so"
        p = parse_file(so)
        self.assertEqual(p.kind, "elf")
        self.assertEqual(p.arch, "arm64")
        self.assertEqual(p.bitness, 64)

    def test_reject_garbage(self):
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(b"not a binary at all")
            name = f.name
        with self.assertRaises(ValueError):
            parse_file(name)
        Path(name).unlink()


if __name__ == "__main__":
    unittest.main()
