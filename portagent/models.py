"""Typed data model shared by every PortAgent stage.

The JSON shape of :class:`StepReport` is the contract required by the mission
spec (section 8): every stage returns step/input/action/artifacts/result/next/
memory_update.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class Engine(str, enum.Enum):
    """Detected game engine family."""

    UNITY = "unity"
    UNREAL = "unreal"
    GAMEMAKER = "gamemaker"
    RPGMAKER = "rpgmaker"
    GODOT = "godot"
    SOURCE = "source"
    CUSTOM_NATIVE = "custom_native"
    DOTNET = "dotnet"
    JAVA = "java"
    PYTHON = "python"
    UNKNOWN = "unknown"


class ApiKind(str, enum.Enum):
    """A runtime API / subsystem a port must satisfy."""

    D3D9 = "d3d9"
    D3D11 = "d3d11"
    D3D12 = "d3d12"
    OPENGL = "opengl"
    VULKAN = "vulkan"
    XAUDIO = "xaudio"
    DIRECTSOUND = "directsound"
    WINDOWS_MME = "winmm"
    WIN32 = "win32"
    SDL2 = "sdl2"
    DOTNET_IL = "dotnet_il"
    JVM = "jvm"
    CPYTHON = "cpython"
    MONO = "mono"
    IL2CPP = "il2cpp"


class StepName(str, enum.Enum):
    ANALYZE = "ANALYZE"
    CHOOSE = "CHOOSE"
    DECOMPILE = "DECOMPILE"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    LEARN = "LEARN"
    ACCEPT = "ACCEPT"
    IMPOSSIBLE = "IMPOSSIBLE"


class Result(str, enum.Enum):
    OK = "ok"
    FAIL = "fail"
    PARTIAL = "partial"
    ACCEPT = "accept"
    IMPOSSIBLE = "impossible"


class Verdict(str, enum.Enum):
    """Outcome of a verification attempt."""

    PASS = "pass"
    FAIL = "fail"
    IMPOSSIBLE = "impossible"
    UNKNOWN = "unknown"


class PortStrategy(str, enum.Enum):
    """The concrete porting route chosen for a game."""

    UNITY_IL2CPP_NDK = "unity_il2cpp_ndk"
    UNITY_MONO_MONOEMBED = "unity_mono_monoembed"
    UNREAL_ANDROID_NDK = "unreal_android_ndk"
    GODOT_ANDROID = "godot_android"
    DOTNET_TRANSPILE = "dotnet_transpile"
    DOTNET_MONO_EMBED = "dotnet_mono_embed"
    JAVA_DEX_REPACK = "java_dex_repack"
    NATIVE_DBT_BOX64 = "native_dbt_box64"
    WINE_WINLATOR = "wine_winlator"
    GM_HTML5_WRAP = "gm_html5_wrap"
    RM_ENGINE_PORT = "rm_engine_port"
    UNSUPPORTED = "unsupported"


#: Strategies that are strictly more faithful than pure emulation.  The policy
#: tries them in order and only falls back to DBT/Wine when nothing else fits.
STRATEGY_PREFERENCE: tuple[PortStrategy, ...] = (
    PortStrategy.UNITY_IL2CPP_NDK,
    PortStrategy.UNITY_MONO_MONOEMBED,
    PortStrategy.UNREAL_ANDROID_NDK,
    PortStrategy.GODOT_ANDROID,
    PortStrategy.DOTNET_TRANSPILE,
    PortStrategy.DOTNET_MONO_EMBED,
    PortStrategy.JAVA_DEX_REPACK,
    PortStrategy.GM_HTML5_WRAP,
    PortStrategy.RM_ENGINE_PORT,
    PortStrategy.NATIVE_DBT_BOX64,
    PortStrategy.WINE_WINLATOR,
)


@dataclass
class BinaryInfo:
    """One executable / library discovered inside the game folder."""

    path: str
    kind: str  # "pe" | "elf" | "unknown"
    arch: str  # "x86" | "x86_64" | "arm64" | "arm" | "unknown"
    machine: int | None = None
    bitness: int | None = None
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    size: int = 0
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class Impossibility:
    """A hard blocker that makes a port provably impossible for this class."""

    reason: str
    evidence: str
    severity: str = "hard"  # "hard" (kernel DRM/anti-cheat) | "legal" | "soft"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class GameProfile:
    """The Analyzer's JSON report about a game."""

    root: str
    engine: Engine = Engine.UNKNOWN
    engine_confidence: float = 0.0
    engine_evidence: list[str] = field(default_factory=list)
    runtime_apis: list[ApiKind] = field(default_factory=list)
    graphics_api: ApiKind | None = None
    audio_api: ApiKind | None = None
    arch: str = "unknown"
    bitness: int | None = None
    managed: bool = False
    il2cpp: bool = False
    dependencies: list[str] = field(default_factory=list)
    binaries: list[BinaryInfo] = field(default_factory=list)
    assets: dict[str, int] = field(default_factory=dict)
    drm: list[str] = field(default_factory=list)
    anticheat: list[str] = field(default_factory=list)
    blockers: list[Impossibility] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0

    @property
    def feasible(self) -> bool:
        return not any(b.severity == "hard" for b in self.blockers)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["engine"] = self.engine.value
        d["runtime_apis"] = [a.value for a in self.runtime_apis]
        d["graphics_api"] = self.graphics_api.value if self.graphics_api else None
        d["audio_api"] = self.audio_api.value if self.audio_api else None
        d["feasible"] = self.feasible
        return d


@dataclass
class Strategy:
    """A candidate porting route produced by CHOOSE."""

    name: PortStrategy
    score: float
    reasons: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)
    expected_effort: str = "medium"
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["name"] = self.name.value
        return d


@dataclass
class StepReport:
    """The mission-mandated report emitted by every stage."""

    step: StepName
    input: str
    action: str
    result: Result = Result.OK
    artifacts: list[str] = field(default_factory=list)
    next: str = ""
    memory_update: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "iteration": self.iteration,
            "step": self.step.value,
            "input": self.input,
            "action": self.action,
            "artifacts": self.artifacts,
            "result": self.result.value,
            "next": self.next,
            "memory_update": self.memory_update,
            "detail": self.detail,
            "ts": self.ts,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
