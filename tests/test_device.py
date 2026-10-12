import unittest

from portagent import device as D


class GpuClassifyTests(unittest.TestCase):
    def test_mali(self):
        self.assertEqual(D._classify_gpu("Mali-G52 MC2", "Helio G88"), D.MALI)

    def test_adreno(self):
        self.assertEqual(D._classify_gpu("Adreno (TM) 650", "SM8250"), D.ADRENO)

    def test_unknown(self):
        self.assertEqual(D._classify_gpu("", "generic"), D.UNKNOWN_GPU)


class TierTests(unittest.TestCase):
    def test_low(self):
        self.assertEqual(D.DeviceProfile(ram_mb=3000).tier, "low")

    def test_mid(self):
        self.assertEqual(D.DeviceProfile(ram_mb=6000).tier, "mid")

    def test_high(self):
        self.assertEqual(D.DeviceProfile(ram_mb=12000).tier, "high")

    def test_soc_fallback_mid(self):
        self.assertEqual(D.DeviceProfile(soc="Helio G88").tier, "mid")


class TecnoSparkTests(unittest.TestCase):
    """The reported device: Helio G88 (Mali-G52), 8 GB, 1080x2460, Android 13."""

    def _profile(self):
        return D.DeviceProfile(
            name="TECNO KI7", model="TECNO KI7", android=13, soc="Helio G88",
            gpu=D.MALI, ram_mb=8192, width=1080, height=2460, dpi=480,
        )

    def test_tier_is_high_ram(self):
        self.assertEqual(self._profile().tier, "high")

    def test_driver_is_virgl_not_turnip(self):
        t = D.tune(self._profile())
        self.assertEqual(t.driver, "virgl")
        self.assertNotEqual(t.driver, "turnip")

    def test_render_target_is_the_long_edge(self):
        t = D.tune(self._profile())
        # Portrait panel: the long edge (2460) is scaled to the (SoC-capped) target.
        expected = D._LONG_EDGE[D._render_tier(self._profile())]
        self.assertEqual(max(t.render_width, t.render_height), expected)

    def test_weak_gpu_caps_render_below_ram_tier(self):
        # 8 GB would be "high", but a Mali-G52 cannot render at the high target.
        prof = self._profile()
        self.assertEqual(prof.tier, "high")
        self.assertEqual(D._render_tier(prof), "mid")
        t = D.tune(prof)
        self.assertLessEqual(max(t.render_width, t.render_height), 1080)
        self.assertTrue(any("capped" in n for n in t.notes))

    def test_frame_cap_is_set(self):
        t = D.tune(self._profile())
        self.assertGreater(t.fps_cap, 0)
        self.assertEqual(t.env["DXVK_FRAME_RATE"], str(t.fps_cap))

    def test_mali_note_present(self):
        t = D.tune(self._profile())
        self.assertTrue(any("Mali" in n for n in t.notes))


class LowEndTests(unittest.TestCase):
    def test_low_end_uses_compatible_dynarec(self):
        prof = D.DeviceProfile(soc="Helio A", gpu=D.MALI, ram_mb=2048,
                               width=720, height=1600)
        t = D.tune(prof)
        self.assertEqual(t.dynarec, "compatible")
        self.assertEqual(t.env["BOX64_DYNAREC_BIGBLOCK"], "0")
        self.assertLessEqual(t.fps_cap, 30)

    def test_unknown_panel_uses_tier_fallback(self):
        low = D.tune(D.DeviceProfile(soc="Helio A", gpu=D.MALI, ram_mb=2048))
        high = D.tune(D.DeviceProfile(soc="Snapdragon 8 Gen 2", gpu=D.ADRENO,
                                      ram_mb=12288))
        self.assertLess(max(low.render_width, low.render_height),
                        max(high.render_width, high.render_height))


class PropsTests(unittest.TestCase):
    def test_from_props_classifies_and_parses(self):
        props = {
            "ro.product.model": "TECNO KI7",
            "ro.build.version.release": "13",
            "ro.soc.model": "Helio G88",
            "ro.sf.lcd_density": "480",
            "ro.portagent.ram_mb": "8192",
            "ro.product.cpu.abilist": "arm64-v8a,armeabi-v7a",
        }
        p = D.from_props(props)
        self.assertEqual(p.model, "TECNO KI7")
        self.assertEqual(p.android, 13)
        self.assertEqual(p.ram_mb, 8192)
        self.assertEqual(p.gpu, D.MALI)
        self.assertEqual(p.abi, "arm64-v8a")

    def test_resolve_inline_props(self):
        p = D.resolve("ro.product.model=X,ro.soc.model=Helio G88,ro.portagent.ram_mb=4096")
        self.assertEqual(p.gpu, D.MALI)
        self.assertEqual(p.ram_mb, 4096)
        self.assertEqual(p.source, "provided")

    def test_resolve_auto_never_crashes_without_adb(self):
        p = D.resolve("auto")
        self.assertIn(p.source, ("adb", "local"))


if __name__ == "__main__":
    unittest.main()
