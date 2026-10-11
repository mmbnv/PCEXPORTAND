"""Optimizer: choose Android tuning for a ported game.

Given a :class:`~portagent.models.GameProfile` and the chosen strategy, this
decides concrete, checkable optimizations -- ABI set, graphics backend, heap
size, audio configuration, thread/DBT tuning, asset handling -- and emits the
values the packager needs (manifest flags, env vars, gradle knobs).

Nothing here is a promise of FPS; every field is a real build/run setting that
is written into the generated project.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ApiKind, Engine, GameProfile, PortStrategy


@dataclass
class OptimizationPlan:
    abis: list[str] = field(default_factory=lambda: ["arm64-v8a"])
    graphics_backend: str = "gles3"          # gles3 | vulkan
    graphics_translation: str | None = None  # dxvk | vkd3d | none
    heap_mb: int = 512
    large_heap: bool = True
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    low_latency_audio: bool = True
    threads: int = 4
    dbt: bool = False                         # uses Box64 dynamic binary translation
    strip_symbols: bool = True
    extract_native_libs: bool = True
    compress_assets: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "abis": self.abis,
            "graphics_backend": self.graphics_backend,
            "graphics_translation": self.graphics_translation,
            "heap_mb": self.heap_mb,
            "large_heap": self.large_heap,
            "audio": {"sample_rate": self.audio_sample_rate, "channels": self.audio_channels,
                      "low_latency": self.low_latency_audio},
            "threads": self.threads,
            "dbt": self.dbt,
            "strip_symbols": self.strip_symbols,
            "extract_native_libs": self.extract_native_libs,
            "compress_assets": self.compress_assets,
            "notes": self.notes,
        }

    def env(self) -> dict[str, str]:
        """Environment variables handed to the runtime layer."""
        env = {
            "PAGENT_AUDIO_RATE": str(self.audio_sample_rate),
            "PAGENT_AUDIO_CHANNELS": str(self.audio_channels),
            "PAGENT_GRAPHICS_BACKEND": self.graphics_backend,
            "PAGENT_THREADS": str(self.threads),
        }
        if self.dbt:
            env["BOX64_DYNAREC_BIGBLOCK"] = "2"
            env["BOX64_DYNAREC_STRONGMEM"] = "1"
            env["BOX64_DYNAREC_SAFEFLAGS"] = "1"
            env["BOX64_DYNAREC_FASTNAN"] = "1"
            env["BOX64_DYNAREC_FASTROUND"] = "1"
            env["BOX64_DYNAREC_X87DOUBLE"] = "1"
            env["BOX64_MAX_CPU"] = str(self.threads)
            if self.graphics_translation == "dxvk":
                env["DXVK_ASYNC"] = "1"
                env["DXVK_STATE_CACHE"] = "1"
        return env


class Optimizer:
    def plan(self, profile: GameProfile, strategy: PortStrategy) -> OptimizationPlan:
        p = OptimizationPlan()
        # Native engines ship first-party Android exporters -> Vulkan where sane.
        if profile.engine in (Engine.GODOT, Engine.UNITY, Engine.UNREAL):
            p.graphics_backend = "vulkan"
            p.notes.append("native engine: prefer Vulkan backend (GLES3 fallback)")
        if profile.graphics_api in (ApiKind.D3D9, ApiKind.D3D11, ApiKind.D3D12):
            p.graphics_translation = "dxvk" if profile.graphics_api != ApiKind.D3D12 else "vkd3d"
            p.notes.append(f"{profile.graphics_api.value} -> {p.graphics_translation} translation layer")
        if strategy in (PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR):
            p.dbt = True
            p.notes.append("DBT path: Box64 tuning enabled; expect lower FPS than native")
        if strategy == PortStrategy.WINE_WINLATOR:
            p.heap_mb = 1024
            p.notes.append("Wine/DXVK needs a larger heap")
        # 32-bit x86 titles are light; 64-bit D3D12/Wine titles are heavy.
        if profile.arch == "x86":
            p.heap_mb = min(p.heap_mb, 512)
        if profile.total_size > 8 * 1024**3:
            p.compress_assets = True
            p.notes.append("large game (>8 GiB): assets compressed in the APK")
        return p
