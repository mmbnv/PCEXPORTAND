"""Runtime layer: how the ported game is actually launched on Android.

The packager embeds the game payload and a launcher Activity; this module decides
*what that Activity runs* and with which environment.  There are four runtime
classes, chosen from the analyzed profile and the planner's strategy:

  ``engine_native``  engine ships an Android exporter (Godot/Unity/Unreal/...):
                     the Activity loads the engine's own ARM64 runtime and opens
                     the game's data pack.
  ``mono_managed``   .NET/Mono title: the Activity boots a Mono runtime and
                     loads the managed assemblies recovered from the game.
  ``dbt_box64``      native x86/x64 title: Box64 dynamic binary translation runs
                     the original executable on ARM64.
  ``wine_dxvk``      native Windows title with D3D: Box64 + Wine + DXVK/VKD3D.

The runtime *binaries* are large ARM64 (and x86-64-under-Box64) builds that are
not vendored in this repo.  ``RuntimeBundler`` copies them from
``~/.portagent-tools/runtime/<class>/`` when the operator has fetched them with
``python -m portagent.runtimes`` (a pinned Winlator release); otherwise the
config is still complete and the app reports exactly which binary is missing --
it never pretends to run.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .models import ApiKind, Engine, GameProfile, PortStrategy
from .optimizer import OptimizationPlan
from .toolpaths import tools_home

# Where an operator drops prebuilt ARM64 runtime payloads.
RUNTIME_HOME = tools_home() / "runtime"

# Runtime libraries each class needs, by filename (matched loosely).
RUNTIME_LIBS: dict[str, list[str]] = {
    "engine_native": ["libgodot_android.so", "libunity.so", "libUnreal.so", "libmain.so"],
    "mono_managed": ["libmonosgen-2.0.so", "libmono-native.so", "libmonobdwgc-2.0.so"],
    "dbt_box64": ["libbox64.so", "box64"],
    "wine_dxvk": ["box64", "wine", "wineserver"],
}

# Engine titles whose sources we do NOT have cannot be re-exported to Android;
# they run through the Box64 + Wine + DXVK container like any other x86 game.
_ENGINE_RUNTIME = {
    Engine.GODOT: "wine_dxvk",
    Engine.UNITY: "wine_dxvk",
    Engine.UNREAL: "wine_dxvk",
    Engine.GAMEMAKER: "wine_dxvk",
    Engine.RPGMAKER: "wine_dxvk",
}


@dataclass
class RuntimeConfig:
    runtime_class: str
    abi: str = "arm64-v8a"
    entry: str | None = None
    payload_dir: str = "game"
    backend: str = "gles3"
    translation: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    libs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "runtime_class": self.runtime_class,
            "abi": self.abi,
            "entry": self.entry,
            "payload_dir": self.payload_dir,
            "backend": self.backend,
            "translation": self.translation,
            "env": self.env,
            "libs": self.libs,
            "notes": self.notes,
        }


class RuntimePlanner:
    def plan(self, profile: GameProfile, strategy: PortStrategy,
             opt: OptimizationPlan) -> RuntimeConfig:
        cfg = RuntimeConfig(
            runtime_class="engine_native",
            abi=opt.abis[0] if opt.abis else "arm64-v8a",
            backend=opt.graphics_backend,
            translation=opt.graphics_translation,
            env=opt.env(),
        )
        cfg.env["PAGENT_ENGINE"] = profile.engine.value
        cfg.env["PAGENT_STRATEGY"] = strategy.value

        if profile.engine in _ENGINE_RUNTIME:
            cfg.runtime_class = _ENGINE_RUNTIME[profile.engine]
            cfg.entry = self._entry(profile)
            cfg.notes.append(
                f"{profile.engine.value}: no sources, so run the shipped x86/x64 build "
                "through the Box64 + Wine + DXVK container"
            )
        elif profile.managed or strategy == PortStrategy.DOTNET_TRANSPILE:
            cfg.runtime_class = "mono_managed"
            cfg.entry = self._managed_entry(profile)
            cfg.notes.append("managed title: boot Mono and load the recovered assemblies")
        elif strategy in (PortStrategy.NATIVE_DBT_BOX64, PortStrategy.UNREAL_ANDROID_NDK):
            cfg.runtime_class = "dbt_box64"
            cfg.entry = self._entry(profile)
            cfg.notes.append("native x86/x64: run under Box64 dynamic binary translation")
        elif strategy == PortStrategy.WINE_WINLATOR:
            cfg.runtime_class = "wine_dxvk"
            cfg.entry = self._entry(profile)
            cfg.notes.append("Windows + D3D: Box64 + Wine + DXVK translation stack")
        else:
            # Fall back sensibly: managed -> mono, else DBT for native code.
            if profile.managed:
                cfg.runtime_class = "mono_managed"
            else:
                cfg.runtime_class = "dbt_box64"
            cfg.entry = self._entry(profile)

        if cfg.runtime_class == "dbt_box64" and profile.graphics_api in (
            ApiKind.D3D9, ApiKind.D3D11, ApiKind.D3D12
        ):
            # D3D through Box64 still needs a translation layer.
            cfg.translation = cfg.translation or (
                "vkd3d" if profile.graphics_api == ApiKind.D3D12 else "dxvk"
            )
        cfg.libs = list(RUNTIME_LIBS.get(cfg.runtime_class, []))
        return cfg

    def _entry(self, profile: GameProfile) -> str | None:
        exes = [b for b in profile.binaries
                if b.path.lower().endswith(".exe") and b.imports]
        if not exes:
            exes = [b for b in profile.binaries if b.path.lower().endswith(".exe")]
        if not exes:
            return None
        return max(exes, key=lambda b: b.size).path

    def _managed_entry(self, profile: GameProfile) -> str | None:
        for b in profile.binaries:
            p = b.path.lower()
            if p.endswith(".dll") and any(k in p for k in ("assembly-csharp", "sts2", "game")):
                return b.path
        for b in profile.binaries:
            if b.path.lower().endswith(".dll"):
                return b.path
        return self._entry(profile)


class RuntimeBundler:
    """Copies prebuilt ARM64 runtime payloads into the APK assets tree."""

    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else RUNTIME_HOME

    def available(self, runtime_class: str) -> bool:
        d = self.home / runtime_class
        return d.is_dir() and any(d.iterdir())

    def bundle(self, cfg: RuntimeConfig, dest: Path) -> list[str]:
        """Copy ``~/.portagent-tools/runtime/<class>/`` into ``dest``.

        Returns the list of copied files.  Empty when nothing is installed --
        which is honest: the app will then report the missing runtime by name.
        """
        src = self.home / cfg.runtime_class
        if not src.is_dir():
            return []
        dest.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for item in src.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(src)
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, out)
            copied.append(str(rel))
        return copied


def write_config(cfg: RuntimeConfig, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "env.json"
    p.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return p
