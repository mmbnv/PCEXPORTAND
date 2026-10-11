"""Memory component (mission section 4, ``Memory``).

Backed by SQLite so experience survives across runs.  Stores:
  * every attempt (profile -> strategy -> verdict) as an outcome row,
  * reusable patch vectors keyed by a signature,
  * heuristic rules of the form "engine X and API Y -> strategy Z" with a
    running success weight that the policy consults in CHOOSE.

The store is intentionally dependency-free (stdlib ``sqlite3``) and safe to
open concurrently for the single-process orchestrator.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import ApiKind, Engine, GameProfile, PortStrategy, Verdict

SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    signature TEXT NOT NULL,
    engine TEXT NOT NULL,
    graphics_api TEXT,
    arch TEXT,
    strategy TEXT NOT NULL,
    verdict TEXT NOT NULL,
    iteration INTEGER,
    detail TEXT,
    artifacts TEXT
);
CREATE TABLE IF NOT EXISTS patches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    signature TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT,
    payload TEXT,
    success INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    graphics_api TEXT,
    strategy TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.0,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    UNIQUE(engine, graphics_api, strategy)
);
CREATE INDEX IF NOT EXISTS idx_attempts_sig ON attempts(signature);
CREATE INDEX IF NOT EXISTS idx_patches_sig ON patches(signature);
"""


def profile_signature(profile: GameProfile) -> str:
    """A stable key describing the *class* of a game for rule lookup."""
    gfx = profile.graphics_api.value if profile.graphics_api else "none"
    mode = "il2cpp" if profile.il2cpp else ("managed" if profile.managed else "native")
    return f"{profile.engine.value}|{gfx}|{profile.arch}|{mode}"


@dataclass
class Rule:
    engine: str
    graphics_api: str | None
    strategy: str
    weight: float
    successes: int
    failures: int

    @property
    def confidence(self) -> float:
        n = self.successes + self.failures
        if n == 0:
            return 0.0
        # Laplace-smoothed success rate, blended with the learned weight.
        rate = (self.successes + 1) / (n + 2)
        return round(0.5 * rate + 0.5 * self.weight, 4)


@dataclass
class Memory:
    path: str | Path = "portagent_memory.sqlite"

    def __post_init__(self) -> None:
        self.path = str(self.path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- attempts ---------------------------------------------------------
    def record_attempt(
        self,
        profile: GameProfile,
        strategy: PortStrategy,
        verdict: Verdict,
        iteration: int = 0,
        detail: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
    ) -> None:
        sig = profile_signature(profile)
        self._conn.execute(
            "INSERT INTO attempts (ts, signature, engine, graphics_api, arch, strategy,"
            " verdict, iteration, detail, artifacts) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                time.time(),
                sig,
                profile.engine.value,
                profile.graphics_api.value if profile.graphics_api else None,
                profile.arch,
                strategy.value,
                verdict.value,
                iteration,
                json.dumps(detail or {}, ensure_ascii=False),
                json.dumps(artifacts or []),
            ),
        )
        self._conn.commit()
        self._update_rule_from_attempt(profile, strategy, verdict)

    def attempts_for(self, profile: GameProfile) -> list[dict[str, Any]]:
        sig = profile_signature(profile)
        rows = self._conn.execute(
            "SELECT * FROM attempts WHERE signature=? ORDER BY ts DESC", (sig,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- patches ----------------------------------------------------------
    def record_patch(
        self,
        signature: str,
        title: str,
        kind: str,
        payload: dict[str, Any],
        success: bool = True,
    ) -> None:
        self._conn.execute(
            "INSERT INTO patches (ts, signature, title, kind, payload, success)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), signature, title, kind, json.dumps(payload, ensure_ascii=False), int(success)),
        )
        self._conn.commit()

    def patches_for(self, signature: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM patches WHERE signature=? AND success=1 ORDER BY ts DESC",
            (signature,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"] or "{}")
            out.append(d)
        return out

    # -- rules ------------------------------------------------------------
    def _update_rule_from_attempt(
        self, profile: GameProfile, strategy: PortStrategy, verdict: Verdict
    ) -> None:
        gfx = profile.graphics_api.value if profile.graphics_api else None
        engine = profile.engine.value
        row = self._conn.execute(
            "SELECT * FROM rules WHERE engine=? AND ifnull(graphics_api,'')=ifnull(?,'')"
            " AND strategy=?",
            (engine, gfx, strategy.value),
        ).fetchone()
        success = verdict == Verdict.PASS
        if row is None:
            self._conn.execute(
                "INSERT INTO rules (engine, graphics_api, strategy, weight, successes, failures)"
                " VALUES (?,?,?,?,?,?)",
                (engine, gfx, strategy.value, 1.0 if success else -1.0, int(success), int(not success)),
            )
        else:
            s = row["successes"] + int(success)
            f = row["failures"] + int(not success)
            rate = (s + 1) / (s + f + 2)
            self._conn.execute(
                "UPDATE rules SET weight=?, successes=?, failures=? WHERE id=?",
                (round(rate, 4), s, f, row["id"]),
            )
        self._conn.commit()

    def rule_for(self, engine: Engine, graphics_api: ApiKind | None) -> Rule | None:
        gfx = graphics_api.value if graphics_api else None
        row = self._conn.execute(
            "SELECT * FROM rules WHERE engine=? AND ifnull(graphics_api,'')=ifnull(?,'')"
            " ORDER BY weight DESC LIMIT 1",
            (engine.value, gfx),
        ).fetchone()
        if row is None:
            return None
        return Rule(
            engine=row["engine"],
            graphics_api=row["graphics_api"],
            strategy=row["strategy"],
            weight=row["weight"],
            successes=row["successes"],
            failures=row["failures"],
        )

    def all_rules(self) -> list[Rule]:
        rows = self._conn.execute("SELECT * FROM rules ORDER BY weight DESC").fetchall()
        return [
            Rule(r["engine"], r["graphics_api"], r["strategy"], r["weight"], r["successes"], r["failures"])
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        attempts = self._conn.execute("SELECT COUNT(*) c FROM attempts").fetchone()["c"]
        passes = self._conn.execute(
            "SELECT COUNT(*) c FROM attempts WHERE verdict='pass'"
        ).fetchone()["c"]
        patches = self._conn.execute("SELECT COUNT(*) c FROM patches").fetchone()["c"]
        rules = self._conn.execute("SELECT COUNT(*) c FROM rules").fetchone()["c"]
        return {"attempts": attempts, "passes": passes, "patches": patches, "rules": rules}
