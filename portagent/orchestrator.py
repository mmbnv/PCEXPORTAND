"""PortAgent orchestrator: the ANALYZE -> CHOOSE -> EXECUTE -> VERIFY -> LEARN loop.

The orchestrator is the policy from the mission (section 1).  It drives the
component stages, emits one JSON :class:`StepReport` per stage, accumulates
experience in :class:`Memory`, and terminates on ACCEPT (verified success) or
IMPOSSIBLE (hard blocker, or exhausted strategy space).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analyzer import Analyzer
from .builder import Builder
from .decompiler import Decompiler
from .memory import Memory, profile_signature
from .models import (
    Engine,
    GameProfile,
    PortStrategy,
    Result,
    StepName,
    StepReport,
    Verdict,
    write_json,
)
from .planner import Planner
from .optimizer import Optimizer
from .packager import ApkPackager
from .runtime import RuntimePlanner
from .translator import Translator
from .verifier import Verifier, capture_oracle


@dataclass
class RunOutcome:
    status: str  # "accepted" | "impossible" | "exhausted" | "inconclusive"
    iterations: int
    strategy: PortStrategy | None
    profile: GameProfile
    apk: str | None = None
    verify: dict[str, Any] | None = None
    reports: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "iterations": self.iterations,
            "strategy": self.strategy.value if self.strategy else None,
            "apk": self.apk,
            "verify": self.verify,
            "message": self.message,
            "profile": self.profile.to_dict(),
            "reports": self.reports,
        }


class PortAgent:
    def __init__(
        self,
        work_dir: str | Path,
        memory_path: str | Path | None = None,
        oracle: dict[str, str] | None = None,
        max_iterations: int = 6,
        analyze_deep: bool = True,
    ):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.max_iterations = max_iterations
        self.oracle = oracle or {}
        self.memory = Memory(memory_path or (self.work_dir / "memory.sqlite"))
        self.planner = Planner(self.memory)
        self.reports: list[StepReport] = []
        self._profile_engine: str | None = None
        self._env_failure = False
        self._apk_built = False
        self._last_strategy: PortStrategy | None = None
        self._last_verify: dict[str, Any] | None = None

    # -- report helper ----------------------------------------------------
    def _emit(
        self,
        step: StepName,
        inp: str,
        action: str,
        result: Result,
        next_: str,
        iteration: int,
        artifacts: list[str] | None = None,
        memory_update: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> StepReport:
        rep = StepReport(
            step=step,
            input=inp,
            action=action,
            result=result,
            next=next_,
            artifacts=artifacts or [],
            memory_update=memory_update or {},
            detail=detail or {},
            iteration=iteration,
        )
        self.reports.append(rep)
        (self.work_dir / "reports").mkdir(parents=True, exist_ok=True)
        write_json(self.work_dir / "reports" / f"{len(self.reports):03d}_{step.value}.json", rep.to_dict())
        return rep

    # -- main loop --------------------------------------------------------
    def run(self, game_path: str | Path) -> RunOutcome:
        game_path = str(game_path)
        profile: GameProfile | None = None
        tried: set[PortStrategy] = set()
        self._last_apk: str | None = None

        for iteration in range(1, self.max_iterations + 1):
            # --- ANALYZE ---
            if profile is None:
                profile = Analyzer(game_path, deep=True).analyze()
                self._profile_engine = profile.engine.value
                prof_path = write_json(self.work_dir / "profile.json", profile.to_dict())
                self._emit(
                    StepName.ANALYZE,
                    inp=game_path,
                    action="scan files, parse PE/ELF, detect engine/APIs/DRM",
                    result=Result.OK if profile.feasible else Result.FAIL,
                    next_="CHOOSE" if profile.feasible else "IMPOSSIBLE",
                    iteration=iteration,
                    artifacts=[str(prof_path)],
                    detail=profile.to_dict(),
                )
                if not profile.feasible:
                    self._emit(
                        StepName.IMPOSSIBLE,
                        inp=profile_signature(profile),
                        action="declare port impossible",
                        result=Result.IMPOSSIBLE,
                        next_="STOP",
                        iteration=iteration,
                        detail={"blockers": [b.to_dict() for b in profile.blockers]},
                    )
                    self.memory.record_attempt(profile, PortStrategy.UNSUPPORTED, Verdict.IMPOSSIBLE, iteration)
                    return self._finish("impossible", iteration, None, profile)

            # --- CHOOSE ---
            strategies = self.planner.choose(profile)
            strat_path = write_json(
                self.work_dir / f"strategies_iter{iteration}.json",
                [s.to_dict() for s in strategies],
            )
            chosen = next((s for s in strategies if s.name not in tried), None)
            if chosen is None or chosen.name == PortStrategy.UNSUPPORTED:
                self._emit(
                    StepName.IMPOSSIBLE,
                    inp=profile_signature(profile),
                    action="strategy space exhausted",
                    result=Result.IMPOSSIBLE,
                    next_="STOP",
                    iteration=iteration,
                    detail={"tried": [t.value for t in tried]},
                )
                status = "exhausted" if tried else "impossible"
                return self._finish(status, iteration, self._last_strategy, profile,
                                    verify=self._last_verify)
            tried.add(chosen.name)
            self._emit(
                StepName.CHOOSE,
                inp=profile_signature(profile),
                action=f"select strategy {chosen.name.value} (score={chosen.score})",
                result=Result.OK,
                next_="EXECUTE",
                iteration=iteration,
                artifacts=[str(strat_path)],
                detail={"chosen": chosen.to_dict(), "candidates": [s.to_dict() for s in strategies]},
            )

            # --- DECOMPILE ---
            dec_dir = self.work_dir / f"iter{iteration}_{chosen.name.value}" / "decompiled"
            dec = Decompiler(dec_dir).decompile(profile)
            dec_path = write_json(dec_dir / "decompile_result.json", dec.to_dict())
            self._emit(
                StepName.DECOMPILE,
                inp=chosen.name.value,
                action="recover code: monodis (IL) / objdump (native asm) / jadx (dex) / Il2CppDumper",
                result=Result.OK if dec.status == "ok" else Result.PARTIAL,
                next_="EXECUTE",
                iteration=iteration,
                artifacts=[str(dec_path)] + list(dec.outputs.values()),
                detail=dec.to_dict(),
            )

            # --- EXECUTE ---
            exec_dir = self.work_dir / f"iter{iteration}_{chosen.name.value}"
            translated = Translator(exec_dir).translate(profile, chosen.name)
            plan = Optimizer().plan(profile, chosen.name)
            runtime = RuntimePlanner().plan(profile, chosen.name, plan)
            build = Builder(exec_dir).build(profile, chosen.name, translated)
            pkg = ApkPackager(exec_dir / "apk").package(
                game_path, translated, plan, app_label=Path(game_path).name, runtime=runtime
            )
            apk = pkg.apk or build.apk
            if apk:
                self._last_apk = apk
            if pkg.status == "ok":
                self._apk_built = True
            if "missing" in str(pkg.steps.get("toolchain", "")):
                # No point retrying other strategies: every build path needs the SDK.
                self._env_failure = True
            build_path = write_json(exec_dir / "build_result.json", build.to_dict())
            pkg_path = write_json(exec_dir / "package_result.json", pkg.to_dict())
            if apk and pkg.apk:
                build.apk = apk
            exec_result = (
                Result.OK if pkg.status == "ok"
                else Result.PARTIAL if (pkg.status == "partial" or build.status in ("ok", "partial"))
                else Result.FAIL
            )
            self._emit(
                StepName.EXECUTE,
                inp=chosen.name.value,
                action="translate APIs, generate shims, optimize, build signed APK",
                result=exec_result,
                next_="VERIFY",
                iteration=iteration,
                artifacts=[str(build_path), str(pkg_path)] + build.objects + ([apk] if apk else []),
                detail={
                    "translated": translated["mapping"],
                    "build": build.to_dict(),
                    "package": pkg.to_dict(),
                    "optimization": plan.to_dict(),
                    "runtime": runtime.to_dict(),
                },
            )

            # --- VERIFY ---
            verify = Verifier(exec_dir, oracle=self.oracle).verify(
                translated, build.objects, build.status, apk_signed=pkg.signed
            )
            verify_path = write_json(exec_dir / "verify_result.json", verify.to_dict())
            self._last_strategy = chosen.name
            self._last_verify = verify.to_dict()
            self._emit(
                StepName.VERIFY,
                inp=chosen.name.value,
                action="determinism + scene equivalence (menu/gameplay/save) vs oracle",
                result=(
                    Result.OK if verify.verdict == Verdict.PASS
                    else (Result.PARTIAL if verify.verdict == Verdict.UNKNOWN else Result.FAIL)
                ),
                next_="LEARN" if verify.verdict != Verdict.PASS else "ACCEPT",
                iteration=iteration,
                artifacts=[str(verify_path)],
                detail=verify.to_dict(),
            )

            # --- LEARN ---
            self.memory.record_attempt(
                profile, chosen.name, verify.verdict, iteration,
                detail={"build_status": build.status, "apk": build.apk},
                artifacts=build.objects,
            )
            if verify.verdict == Verdict.PASS:
                self.memory.record_patch(
                    profile_signature(profile),
                    title=f"strategy {chosen.name.value} verified",
                    kind="strategy",
                    payload={"strategy": chosen.name.value, "mapping": translated["mapping"]},
                    success=True,
                )
            self._emit(
                StepName.LEARN,
                inp=chosen.name.value,
                action="record outcome + update heuristics",
                result=Result.OK,
                next_=(
                    "ACCEPT" if verify.verdict == Verdict.PASS
                    else "STOP(environment)" if self._env_failure
                    else "CHOOSE(next strategy)"
                ),
                iteration=iteration,
                memory_update={
                    "recorded": chosen.name.value,
                    "verdict": verify.verdict.value,
                    "stats": self.memory.stats(),
                },
            )

            if self._env_failure:
                # A missing SDK blocks every strategy; do not burn the budget.
                return self._finish("exhausted", iteration, chosen.name, profile)

            if verify.verdict == Verdict.PASS:
                self._emit(
                    StepName.ACCEPT,
                    inp=chosen.name.value,
                    action="port verified equivalent; accept",
                    result=Result.ACCEPT,
                    next_="STOP",
                    iteration=iteration,
                    artifacts=[build.apk] if build.apk else [],
                    detail={"apk": build.apk, "verify": verify.to_dict()},
                )
                return self._finish("accepted", iteration, chosen.name, profile, build.apk, verify.to_dict())

        # Loop budget exhausted without success.
        return self._finish("inconclusive", self.max_iterations, self._last_strategy, profile,
                            verify=self._last_verify)

    def _finish(
        self,
        status: str,
        iterations: int,
        strategy: PortStrategy | None,
        profile: GameProfile,
        apk: str | None = None,
        verify: dict[str, Any] | None = None,
        message: str = "",
    ) -> RunOutcome:
        outcome = RunOutcome(
            status=status,
            iterations=iterations,
            strategy=strategy,
            profile=profile,
            apk=apk or getattr(self, "_last_apk", None),
            verify=verify,
            reports=[r.to_dict() for r in self.reports],
            message=message or self._describe(status, strategy),
        )
        write_json(self.work_dir / "run_outcome.json", outcome.to_dict())
        return outcome

    def _describe(self, status: str, strategy: PortStrategy | None) -> str:
        """A one-line, human-readable reason for the final status."""
        eng = self._profile_engine or "?"
        if status == "accepted":
            return f"port verified equivalent ({strategy.value if strategy else eng})"
        if status == "impossible":
            return "hard blocker: no Android-compatible path exists for this title"
        strat = strategy.value if strategy else "none"
        if self._env_failure:
            return (
                f"environment incomplete: {strat} needs an Android SDK/NDK and host "
                "build tools that are not installed - install them and re-run"
            )
        if self._apk_built:
            return (
                "APK built, but equivalence could not be proven without an oracle trace "
                "- supply one to reach PASS"
            )
        return (
            f"no verified port for {eng} after {self.max_iterations} iteration(s); "
            "strategies that need an Android SDK/NDK were blocked by the environment"
        )
