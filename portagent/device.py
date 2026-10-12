"""Device-aware tuning: make a port fit the phone it will run on.

The heavy part of a port (Box64 + Wine + DXVK) is only usable on a phone if the
build matches the device's RAM, SoC tier, GPU vendor and screen.  This module
detects a device -- from a connected ``adb`` device, an explicit profile, or the
local machine for desktop builds -- and turns it into concrete, checkable
settings: render resolution, frame cap, heap, dynarec level and the right GPU
driver.

Nothing here promises FPS.  Every field is written into the generated Activity
or the runtime environment; the Activity re-detects the device at run time so
the same APK adapts to whatever phone installs it.

The rules encode what actually matters on the target class of device
(mid-range ARM with a Mali/Adreno GPU, e.g. a Tecno Spark 10 Pro / Helio G88):

* Render at a fraction of the panel and let the GPU upscale.  A 1080x2460 panel
  is 2.6x the pixels of 720x1600; rendering natively melts a G88.
* Cap the frame rate.  An uncapped DXVK loop pegs every core, thermally
  throttles the whole SoC and makes the *phone* (not just the game) lag.
* Pick a GPU driver that matches the vendor.  Turnip only drives Adreno; on
  Mali the only working paths are the system Vulkan driver or VirGL/Zink.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Vendor -> the Vulkan/GL translation a phone can actually use.
MALI = "mali"
ADRENO = "adreno"
POWERV_R = "powervr"
XCLIPSE = "xclipse"
UNKNOWN_GPU = "unknown"

# Driver components a runtime bundle may provide, by GPU vendor.
DRIVER_FOR_VENDOR: dict[str, str] = {
    MALI: "virgl",        # freedreno/Turnip cannot drive Mali
    ADRENO: "turnip",
    XCLIPSE: "turnip",    # Xclipse is an AMD RDNA GPU in a Samsung SoC
    POWERV_R: "virgl",
    UNKNOWN_GPU: "virgl",  # VirGL/Zink is the vendor-neutral fallback
}


@dataclass
class DeviceProfile:
    """The subset of a device we tune against."""

    name: str = "unknown"
    model: str = "unknown"
    android: int = 0
    soc: str = "unknown"
    gpu: str = UNKNOWN_GPU
    ram_mb: int = 0
    width: int = 0
    height: int = 0
    dpi: int = 0
    abi: str = "arm64-v8a"
    source: str = "unknown"  # adb | provided | local | default

    @property
    def tier(self) -> str:
        """low | mid | high, from RAM (the most reliable cheap signal)."""
        if self.ram_mb and self.ram_mb < 4000:
            return "low"
        if self.ram_mb and self.ram_mb < 7000:
            return "mid"
        if self.ram_mb >= 7000:
            return "high"
        # No RAM info: infer from the SoC name.
        soc = self.soc.lower()
        if any(k in soc for k in ("helio g", "snapdragon 4", "snapdragon 6",
                                  "unisoc", "exynos 8", "tensor")):
            return "mid"
        if any(k in soc for k in ("snapdragon 8", "dimensity 9", "exynos 2")):
            return "high"
        return "mid"

    @property
    def pixels(self) -> int:
        return self.width * self.height

    def to_dict(self) -> dict:
        return {
            "name": self.name, "model": self.model, "android": self.android,
            "soc": self.soc, "gpu": self.gpu, "ram_mb": self.ram_mb,
            "width": self.width, "height": self.height, "dpi": self.dpi,
            "abi": self.abi, "tier": self.tier, "source": self.source,
        }


def _classify_gpu(gpu: str, soc: str) -> str:
    blob = f"{gpu} {soc}".lower()
    if "mali" in blob:
        return MALI
    if "adreno" in blob:
        return ADRENO
    if "powervr" in blob or "power vr" in blob:
        return POWERV_R
    if "xclipse" in blob:
        return XCLIPSE
    # The renderer string is only available on-device; from build-time props we
    # fall back to a SoC-family table (Helio/Dimensity/Exynos/Unisoc use Mali or
    # Immortalis; Snapdragon uses Adreno; Tensor uses Mali).
    if any(k in blob for k in ("helio", "dimensity", "exynos", "unisoc", "tensor",
                               "mt6", "mt8", "kirin")):
        return MALI
    if any(k in blob for k in ("snapdragon", "sm8", "sm7", "sm6", "msm", "qcom")):
        return ADRENO
    return UNKNOWN_GPU


def from_props(props: dict[str, str], *, source: str = "provided") -> DeviceProfile:
    """Build a profile from an Android ``getprop``-style mapping."""

    def g(*keys: str) -> str:
        for k in keys:
            v = props.get(k)
            if v:
                return v.strip()
        return ""

    soc = g("ro.soc.model", "ro.board.platform", "ro.hardware", "ro.chipname")
    gpu = g("ro.hardware.vulkan", "ro.hardware.egl", "ro.gfx.driver")
    model = g("ro.product.model", "ro.product.vendor.model")
    name = g("ro.product.marketname", "ro.product.name") or model
    try:
        android = int(re.sub(r"\D", "", g("ro.build.version.release")) or 0)
    except ValueError:
        android = 0
    width = height = dpi = 0
    size = g("ro.sf.lcd_density")  # only dpi reliably here; wm size is queried live
    if size.isdigit():
        dpi = int(size)
    ram = g("ro.config.low_ram")
    ram_mb = 0
    total = g("ro.portagent.ram_mb", "dalvik.vm.heapsize")
    if total.isdigit():
        ram_mb = int(total)
    abis = g("ro.product.cpu.abilist")
    abi = "arm64-v8a" if "arm64" in abis else (abis.split(",")[0] if abis else "arm64-v8a")
    prof = DeviceProfile(
        name=name or "unknown", model=model or "unknown", android=android,
        soc=soc or "unknown", gpu=_classify_gpu(gpu, soc), ram_mb=ram_mb,
        width=width, height=height, dpi=dpi, abi=abi, source=source,
    )
    if ram == "true" and not prof.ram_mb:
        prof.ram_mb = 2048
    return prof


def _parse_wm_size(out: str) -> tuple[int, int]:
    m = re.search(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", out)
    if not m:
        m = re.search(r"(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def detect(*, adb: str | None = None, serial: str | None = None,
           timeout: float = 8.0) -> DeviceProfile | None:
    """Detect a connected device via ``adb``; None if unavailable.

    Returns None (never a guess) when adb is missing or no device is attached,
    so callers fall back to an explicit profile or to local defaults.
    """
    import shutil
    import subprocess

    exe = adb or shutil.which("adb")
    if not exe:
        return None
    base = [exe]
    if serial:
        base += ["-s", serial]

    def sh(args: list[str]) -> str:
        try:
            r = subprocess.run(base + args, capture_output=True, text=True, timeout=timeout)
            return r.stdout if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    if "device" not in sh(["get-state"]):
        return None
    prop_keys = [
        "ro.product.model", "ro.product.marketname", "ro.product.name",
        "ro.build.version.release", "ro.soc.model", "ro.board.platform",
        "ro.hardware", "ro.hardware.vulkan", "ro.hardware.egl",
        "ro.sf.lcd_density", "ro.product.cpu.abilist",
    ]
    props: dict[str, str] = {}
    for k in prop_keys:
        props[k] = sh(["shell", "getprop", k])
    # Real total RAM (getprop has no portable key for it).
    mem = sh(["shell", "cat", "/proc/meminfo"])
    m = re.search(r"MemTotal:\s*(\d+)\s*kB", mem)
    if m:
        props["ro.portagent.ram_mb"] = str(int(m.group(1)) // 1024)
    w, h = _parse_wm_size(sh(["shell", "wm", "size"]))
    prof = from_props(props, source="adb")
    prof.width, prof.height = w or prof.width, h or prof.height
    return prof


def local_profile() -> DeviceProfile:
    """A sane default for a desktop (non-device) build."""
    import os

    ram_mb = 0
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        ram_mb = int(pages * size / (1024 * 1024))
    except (ValueError, OSError, AttributeError):
        pass
    return DeviceProfile(
        name="desktop", model="desktop", soc="desktop", gpu=UNKNOWN_GPU,
        ram_mb=ram_mb, abi="arm64-v8a", source="local",
    )


@dataclass
class DeviceTuning:
    """Concrete settings derived from a DeviceProfile."""

    render_width: int
    render_height: int
    fps_cap: int
    heap_mb: int
    dynarec: str          # fast | balanced | compatible
    driver: str           # turnip | virgl | system
    scale: float
    env: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "render": f"{self.render_width}x{self.render_height}",
            "scale": round(self.scale, 3), "fps_cap": self.fps_cap,
            "heap_mb": self.heap_mb, "dynarec": self.dynarec,
            "driver": self.driver, "env": self.env, "notes": self.notes,
        }


# Target render long edge by tier.  Upscaling to the panel keeps the GPU load
# proportional to the SoC, not the screen.
_LONG_EDGE = {"low": 720, "mid": 1080, "high": 1440}
_FPS_CAP = {"low": 30, "mid": 40, "high": 60}
_HEAP = {"low": 512, "mid": 1024, "high": 2048}
_DYNAREC = {"low": "compatible", "mid": "balanced", "high": "fast"}
# 16:9 fallback render size when the panel size is unknown.
_FALLBACK_WH = {"low": (960, 540), "mid": (1280, 720), "high": (1600, 900)}

# Weak mobile GPUs: RAM alone would mis-tier these (a Helio G88 has 8 GB but a
# Mali-G52 MP2 that cannot push a 1440p DXVK frame).  Cap the *render* tier.
_WEAK_SOC_HINTS = ("helio g2", "helio g3", "helio g5", "helio g8",
                   "helio a", "helio p", "snapdragon 4", "snapdragon 6",
                   "unisoc", "exynos 7", "exynos 8")


def _render_tier(profile: DeviceProfile) -> str:
    tier = profile.tier
    soc = profile.soc.lower()
    if any(h in soc for h in _WEAK_SOC_HINTS):
        # Never render a weak GPU at the top tier; cap the resolution even if RAM
        # would allow more.
        if tier == "high":
            tier = "mid"
    return tier


def tune(profile: DeviceProfile) -> DeviceTuning:
    tier = profile.tier
    rtier = _render_tier(profile)
    long_edge = _LONG_EDGE[rtier]
    # Preserve aspect; keep the short edge even.
    if profile.width and profile.height:
        short, long = sorted((profile.width, profile.height))
        scale = min(1.0, long_edge / long)
        rw, rh = int(short * scale), int(long * scale)
    else:
        rw, rh = _FALLBACK_WH[rtier]
        scale = 1.0
    rw, rh = max(2, rw - rw % 2), max(2, rh - rh % 2)

    driver = DRIVER_FOR_VENDOR.get(profile.gpu, "system")
    t = DeviceTuning(
        render_width=rw, render_height=rh,
        fps_cap=_FPS_CAP[rtier], heap_mb=_HEAP[tier], dynarec=_DYNAREC[tier],
        driver=driver, scale=scale,
    )
    t.notes.append(f"{tier} tier ({profile.ram_mb or '?'} MB, {profile.soc}): "
                   f"render {rw}x{rh}, cap {t.fps_cap} FPS, heap {t.heap_mb} MB")
    if rtier != tier:
        t.notes.append(f"weak GPU in '{profile.soc}': render tier capped to "
                       f"'{rtier}' regardless of RAM")
    if profile.gpu == MALI:
        t.notes.append("Mali GPU: Turnip/freedreno cannot drive it; use the system "
                       "Vulkan driver (or VirGL/Zink)")
    elif profile.gpu == UNKNOWN_GPU:
        t.notes.append("unknown GPU vendor: prefer the system Vulkan driver")

    # Box64 dynarec strength by tier: weaker phones need the compatible mode to
    # avoid recompiler stalls that stall the whole device.
    env = {
        "BOX64_DYNAREC_BIGBLOCK": {"fast": "3", "balanced": "2", "compatible": "0"}[t.dynarec],
        "BOX64_DYNAREC_STRONGMEM": {"fast": "0", "balanced": "1", "compatible": "1"}[t.dynarec],
        "BOX64_DYNAREC_SAFEFLAGS": {"fast": "0", "balanced": "1", "compatible": "2"}[t.dynarec],
        "BOX64_DYNAREC_CALLRET": "1",
        "BOX64_MAX_CPU": str(min(4, max(2, profile.ram_mb // 2048 or 4))),
        # Frame cap + present interval are what stop the phone-wide lag.
        "PAGENT_FPS_CAP": str(t.fps_cap),
        "DXVK_FRAME_RATE": str(t.fps_cap),
        "PAGENT_RENDER_W": str(rw),
        "PAGENT_RENDER_H": str(rh),
        "PAGENT_GPU_DRIVER": t.driver,
    }
    if profile.gpu in (MALI, UNKNOWN_GPU):
        # DXVK's Vulkan path on Mali must go through the system loader.
        env["PAGENT_VULKAN_ICD"] = "system"
    t.env = env
    return t


def resolve(spec: str | None = None, *, adb: str | None = None) -> DeviceProfile:
    """Turn a user-supplied spec into a DeviceProfile.

    ``spec`` accepts:
      * ``None``/``auto``      -> a connected adb device, else the local machine
      * a path to a JSON file  -> a saved ``getprop`` mapping or a DeviceProfile
      * a comma list or JSON   -> inline ``key=value`` props, e.g.
        ``ro.product.model=...,ro.soc.model=...,ro.portagent.ram_mb=8192``
    """
    import json

    if spec and spec not in ("auto", "device"):
        p = Path(spec)
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if "tier" in data and "soc" in data:  # already a DeviceProfile dump
                return DeviceProfile(**{k: v for k, v in data.items() if k in
                                        DeviceProfile.__dataclass_fields__})
            return from_props({str(k): str(v) for k, v in data.items()}, source="provided")
        if "=" in spec:
            props = {}
            for part in spec.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    props[k.strip()] = v.strip()
            return from_props(props, source="provided")

    detected = detect(adb=adb)
    if detected is not None:
        return detected
    return local_profile()


def describe(profile: DeviceProfile, tuning: DeviceTuning) -> str:
    lines = [
        f"device: {profile.name} ({profile.model}) src={profile.source}",
        f"soc: {profile.soc}  gpu: {profile.gpu}  ram: {profile.ram_mb} MB  "
        f"android: {profile.android}  tier: {profile.tier}",
        f"panel: {profile.width}x{profile.height} @ {profile.dpi}dpi  "
        f"-> render {tuning.render_width}x{tuning.render_height} "
        f"(scale {tuning.scale:.2f})",
    ]
    lines += [f"note: {n}" for n in tuning.notes]
    return "\n".join(lines)
