"""CHOOSE stage: pick the best porting strategy for a profile.

The planner combines static engine/API rules with the learned weights stored in
:class:`portagent.memory.Memory`.  A strategy that succeeded on the same
(engine, graphics_api) class is promoted; one that failed is demoted.  Hard
blockers short-circuit to ``UNSUPPORTED``.
"""

from __future__ import annotations

from .memory import Memory, profile_signature
from .models import (
    STRATEGY_PREFERENCE,
    ApiKind,
    Engine,
    GameProfile,
    PortStrategy,
    Strategy,
)

# Base applicability: which strategies are even in the running for an engine.
ENGINE_STRATEGIES: dict[Engine, list[PortStrategy]] = {
    Engine.UNITY: [PortStrategy.UNITY_IL2CPP_NDK, PortStrategy.UNITY_MONO_MONOEMBED],
    Engine.UNREAL: [PortStrategy.UNREAL_ANDROID_NDK],
    Engine.GODOT: [PortStrategy.GODOT_ANDROID],
    Engine.DOTNET: [PortStrategy.DOTNET_TRANSPILE, PortStrategy.DOTNET_MONO_EMBED],
    Engine.JAVA: [PortStrategy.JAVA_DEX_REPACK],
    Engine.GAMEMAKER: [PortStrategy.GM_HTML5_WRAP, PortStrategy.NATIVE_DBT_BOX64],
    Engine.RPGMAKER: [PortStrategy.RM_ENGINE_PORT],
    Engine.SOURCE: [PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR],
    Engine.CUSTOM_NATIVE: [PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR],
    Engine.PYTHON: [PortStrategy.RM_ENGINE_PORT, PortStrategy.NATIVE_DBT_BOX64],
    Engine.UNKNOWN: [PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR],
}

TOOL_REQUIREMENTS: dict[PortStrategy, list[str]] = {
    PortStrategy.UNITY_IL2CPP_NDK: ["Il2CppDumper", "ndk", "gradle", "cmake"],
    PortStrategy.UNITY_MONO_MONOEMBED: ["mono-embed", "ndk", "gradle"],
    PortStrategy.UNREAL_ANDROID_NDK: ["unreal-android", "ndk", "gradle"],
    PortStrategy.GODOT_ANDROID: ["godot-export", "ndk", "gradle"],
    PortStrategy.DOTNET_TRANSPILE: ["ilspy", "kotlin", "gradle"],
    PortStrategy.DOTNET_MONO_EMBED: ["mono-embed", "ndk", "gradle"],
    PortStrategy.JAVA_DEX_REPACK: ["jadx", "apktool", "gradle"],
    PortStrategy.GM_HTML5_WRAP: ["gamemaker-html5", "webview"],
    PortStrategy.RM_ENGINE_PORT: ["runtime-port"],
    PortStrategy.NATIVE_DBT_BOX64: ["box64", "wine", "ndk"],
    PortStrategy.WINE_WINLATOR: ["wine", "box64", "dxvk", "ndk"],
}

EFFORT: dict[PortStrategy, str] = {
    PortStrategy.UNITY_IL2CPP_NDK: "high",
    PortStrategy.UNITY_MONO_MONOEMBED: "medium",
    PortStrategy.UNREAL_ANDROID_NDK: "high",
    PortStrategy.GODOT_ANDROID: "low",
    PortStrategy.DOTNET_TRANSPILE: "high",
    PortStrategy.DOTNET_MONO_EMBED: "medium",
    PortStrategy.JAVA_DEX_REPACK: "low",
    PortStrategy.GM_HTML5_WRAP: "low",
    PortStrategy.RM_ENGINE_PORT: "low",
    PortStrategy.NATIVE_DBT_BOX64: "medium",
    PortStrategy.WINE_WINLATOR: "high",
}


class Planner:
    def __init__(self, memory: Memory | None = None):
        self.memory = memory

    def choose(self, profile: GameProfile) -> list[Strategy]:
        if not profile.feasible:
            return [
                Strategy(
                    name=PortStrategy.UNSUPPORTED,
                    score=0.0,
                    reasons=[b.reason for b in profile.blockers if b.severity == "hard"],
                    required_tools=[],
                    expected_effort="n/a",
                    risks=["hard blocker present"],
                )
            ]

        candidates = list(ENGINE_STRATEGIES.get(profile.engine, ENGINE_STRATEGIES[Engine.UNKNOWN]))
        # API-driven additions.
        if profile.graphics_api in (ApiKind.D3D9, ApiKind.D3D11, ApiKind.D3D12):
            if PortStrategy.WINE_WINLATOR not in candidates:
                candidates.append(PortStrategy.WINE_WINLATOR)

        order = {s: i for i, s in enumerate(STRATEGY_PREFERENCE)}
        learned = self._learned(profile)

        strategies: list[Strategy] = []
        for strat in candidates:
            base = 1.0 - 0.05 * order.get(strat, len(order))
            reasons = [f"engine={profile.engine.value} supports {strat.value}"]
            score = base
            if profile.graphics_api:
                if strat in (PortStrategy.UNITY_IL2CPP_NDK, PortStrategy.UNREAL_ANDROID_NDK,
                             PortStrategy.GODOT_ANDROID):
                    score += 0.1  # native engines export to Vulkan/GLES cleanly
                if strat == PortStrategy.WINE_WINLATOR and profile.graphics_api in (
                    ApiKind.D3D9, ApiKind.D3D11, ApiKind.D3D12
                ):
                    reasons.append(f"{profile.graphics_api.value} needs DXVK/VKD3D translation")
            if profile.il2cpp and strat == PortStrategy.UNITY_MONO_MONOEMBED:
                score -= 1.0  # wrong: IL2CPP build has no managed assemblies to embed
                reasons.append("game is IL2CPP, not Mono - monoembed inapplicable")
            if profile.managed and strat == PortStrategy.UNITY_IL2CPP_NDK:
                score -= 1.0
                reasons.append("game is Mono-managed, IL2CPP path inapplicable")
            if strat in learned:
                boost = learned[strat]
                score += boost
                reasons.append(f"memory boost {boost:+.2f} from prior outcomes")
            strategies.append(
                Strategy(
                    name=strat,
                    score=round(score, 4),
                    reasons=reasons,
                    required_tools=TOOL_REQUIREMENTS.get(strat, []),
                    expected_effort=EFFORT.get(strat, "medium"),
                    risks=self._risks(strat, profile),
                )
            )
        strategies.sort(key=lambda s: -s.score)
        return strategies

    def _learned(self, profile: GameProfile) -> dict[PortStrategy, float]:
        if self.memory is None:
            return {}
        out: dict[PortStrategy, float] = {}
        for rule in self.memory.all_rules():
            if rule.engine != profile.engine.value:
                continue
            gfx = profile.graphics_api.value if profile.graphics_api else None
            if (rule.graphics_api or None) not in (None, gfx):
                continue
            try:
                strat = PortStrategy(rule.strategy)
            except ValueError:
                continue
            # Blend in only when there is actual evidence.
            if rule.successes + rule.failures > 0:
                out[strat] = round((rule.confidence - 0.5) * 1.2, 4)
        return out

    def _risks(self, strat: PortStrategy, profile: GameProfile) -> list[str]:
        risks: list[str] = []
        if strat in (PortStrategy.NATIVE_DBT_BOX64, PortStrategy.WINE_WINLATOR):
            risks.append("dynamic binary translation is slower than native; FPS risk")
            risks.append("graphics must be translated (DXVK/VKD3D) - correctness risk")
        if strat == PortStrategy.UNITY_IL2CPP_NDK:
            risks.append("IL2CPP reconstruction is error-prone; needs symbol recovery")
        if strat == PortStrategy.DOTNET_TRANSPILE:
            risks.append("IL->Kotlin transpilation may break reflection/unsafe code")
        if profile.arch == "unknown":
            risks.append("architecture unknown; DBT compatibility unproven")
        return risks

    def signature(self, profile: GameProfile) -> str:
        return profile_signature(profile)
