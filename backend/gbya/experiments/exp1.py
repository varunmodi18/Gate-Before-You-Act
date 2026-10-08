"""Experiment 1 core: the initial gate decision on a fixed package (plan §A.1, T2.6).

Every gate configuration judges the **identical** package of each case (§L.4 item 5). For each
(case, system, run) the runner builds the episode state from the package — the cited ids are
pre-registered as retrieved, standing in for the frozen prefix's registry until the prefix builder
exists (T4.3) — evaluates once with retries disabled, and stores one ``gate_decisions`` row.

G0 is the reference that admits every well-formed package; it runs through the same gate
(schema only), so there is still one gate implementation. Code-only systems (G0, G1, G2, A1) are
deterministic; systems with C4 are added with the verifier in M3 (T3.5).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from gbya.config import Settings
from gbya.context.models import TrustedContext
from gbya.data.connection import open_case_db
from gbya.gate.checks import GateEnv
from gbya.gate.config import GateConfig, load_configs
from gbya.gate.evidence import window_time_range
from gbya.gate.gate import Gate
from gbya.gate.types import Claim, GateDecision
from gbya.llm.tokens import TokenCounter
from gbya.policy.engine import PolicyEngine
from gbya.store.models import Case, GateDecisionRow, Scenario
from gbya.tools.names import ALL_TOOLS
from gbya.tools.state import EpisodeState

CODE_ONLY = ("G0", "G1", "G2", "A1")


@dataclass(frozen=True)
class Package:
    tool: str
    args: dict[str, Any]
    cited: tuple[int, ...]
    technique_claimed: str | None = None
    rationale: str | None = None  # seen only by the A3 verifier variant (M3)

    def call_args(self) -> dict[str, Any]:
        return {**self.args, "cited": list(self.cited)}


@dataclass(frozen=True)
class Exp1Case:
    case_id: str
    db_path: Path
    context: TrustedContext
    package: Package
    expected: str | None = None  # labels.initial_gate_label ("admit" / "reject")
    toolset: tuple[str, ...] = ALL_TOOLS
    prefix_retrieved: frozenset[int] = field(default_factory=frozenset)

    @property
    def retrieved(self) -> set[int]:
        return set(self.prefix_retrieved or self.package.cited)


def decide(case: Exp1Case, gate: Gate, counter: TokenCounter) -> tuple[GateDecision, float]:
    """One initial gate decision (no retries), with its wall time in ms."""
    con = open_case_db(case.db_path)
    try:
        env = GateEnv(
            con=con,
            ctx=case.context,
            state=EpisodeState(retrieved=case.retrieved),
            counter=counter,
            allowed_tools=case.toolset,
            window=window_time_range(con),
        )
        t0 = time.perf_counter()
        claim = Claim(technique_claimed=case.package.technique_claimed,
                      rationale=case.package.rationale)  # fmt: skip
        decision = gate.evaluate(
            case.package.tool, case.package.call_args(), env, retries=False, claim=claim
        )
        return decision, round((time.perf_counter() - t0) * 1000, 3)
    finally:
        con.close()


def decision_row(
    decision: GateDecision, case: Exp1Case, run_id: int | None, run_idx: int, ms: float
) -> GateDecisionRow:
    return GateDecisionRow(
        run_id=run_id,
        case_id=case.case_id,
        system=decision.config_id,
        run_idx=run_idx,
        episode_id=None,
        call={"tool": case.package.tool, "args": case.package.args},
        cited=list(case.package.cited),
        checks=[c.model_dump(mode="json") for c in decision.checks],
        verdict=decision.verdict.value,
        verifier=decision.verifier.model_dump(mode="json") if decision.verifier else None,
        tokens_in=0,
        tokens_out=0,
        ms=ms,
    )


def run_code_only(
    session: Session,
    cases: Iterable[Exp1Case],
    *,
    policy: PolicyEngine,
    counter: TokenCounter,
    run_id: int | None,
    systems: Sequence[str] = CODE_ONLY,
    run_idx: int = 1,
    configs: dict[str, GateConfig] | None = None,
) -> list[GateDecisionRow]:
    """Evaluate every (case, system) once and add a ``gate_decisions`` row for each."""
    configs = configs or load_configs()
    gates = {}
    for sid in systems:
        if "C4" in configs[sid].checks:
            raise ValueError(f"{sid} needs the verifier (M3); code-only systems: {CODE_ONLY}")
        gates[sid] = Gate(configs[sid], policy)
    rows = []
    for case in sorted(cases, key=lambda c: c.case_id):
        for sid in systems:
            decision, ms = decide(case, gates[sid], counter)
            row = decision_row(decision, case, run_id, run_idx, ms)
            session.add(row)
            rows.append(row)
    session.flush()
    return rows


def load_case(session: Session, case_id: str, settings: Settings) -> Exp1Case:
    """An Exp 1 case from app.db (``cases`` + its scenario's trusted context)."""
    row = session.get(Case, case_id)
    if row is None:
        raise KeyError(case_id)
    scenario = session.get(Scenario, row.scenario_id)
    if scenario is None or scenario.trusted_context is None:
        raise ValueError(f"{case_id}: scenario {row.scenario_id} has no trusted context")
    if not row.case_db_path:
        raise ValueError(f"{case_id}: no case database")
    pkg = row.package
    return Exp1Case(
        case_id=row.id,
        db_path=settings.resolve(Path(row.case_db_path)),
        context=TrustedContext.model_validate(scenario.trusted_context),
        package=Package(
            tool=str(pkg["tool"]),
            args=dict(pkg["args"]),
            cited=tuple(int(i) for i in pkg["cited"]),
            technique_claimed=pkg.get("technique_claimed"),
            rationale=pkg.get("rationale"),
        ),
        expected=(row.labels or {}).get("initial_gate_label"),
    )


def comparable(row: GateDecisionRow) -> dict[str, Any]:
    """A decision row without ids and timings, for determinism checks."""
    checks = [{k: v for k, v in c.items() if k != "duration_ms"} for c in row.checks]
    return {
        "case_id": row.case_id,
        "system": row.system,
        "run_idx": row.run_idx,
        "call": row.call,
        "cited": row.cited,
        "checks": checks,
        "verdict": row.verdict,
        "verifier": row.verifier,
    }
