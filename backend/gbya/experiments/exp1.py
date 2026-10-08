"""Experiment 1 core: the initial gate decision on a fixed package (plan §A.1, T2.6, T3.5).

Every gate configuration judges the **identical** package of each case (§L.4 item 5). For each
(case, system, run) the runner builds the episode state from the package — the cited ids are
pre-registered as retrieved, standing in for the frozen prefix's registry until the prefix builder
exists (T4.3) — evaluates once with retries disabled, and stores one ``gate_decisions`` row.

G0 is the reference that admits every well-formed package; it runs through the same gate
(schema only), so there is still one gate implementation. Code-only systems (G0, G1, G2, A1) are
deterministic.

**Exp 1V** (``judge``): each verifier variant judges every package as the gate would show it —
the same re-read records, rendering, claim and tickets — whatever C1-C3 would say; the call is
stored in ``verifier_evals``. **Exp 1G** (``compose``): systems with C4 run the same
``Gate.evaluate`` with a ``StoredVerifier`` that returns the stored call for the same (case,
variant, run), so composition and an inline gate call share one code path (§D.7.2).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from gbya.cases.models import REdit, effective_context, effective_toolset
from gbya.config import Settings
from gbya.context.models import TrustedContext
from gbya.data.connection import open_case_db
from gbya.gate.checks import GateEnv
from gbya.gate.config import GateConfig, load_configs
from gbya.gate.evidence import read_cited, render_cited, window_time_range
from gbya.gate.gate import Gate
from gbya.gate.types import Claim, GateDecision, VerifierCall
from gbya.gate.verifier import LLMVerifier
from gbya.llm.tokens import TokenCounter
from gbya.policy.engine import PolicyEngine
from gbya.store.models import Case, GateDecisionRow, Scenario, VerifierEval
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


def claim_of(case: Exp1Case) -> Claim:
    return Claim(technique_claimed=case.package.technique_claimed,
                 rationale=case.package.rationale)  # fmt: skip


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
        decision = gate.evaluate(
            case.package.tool, case.package.call_args(), env, retries=False, claim=claim_of(case)
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
        verifier=verifier_summary(decision.verifier_call),
        tokens_in=decision.verifier_call.tokens_in if decision.verifier_call else 0,
        tokens_out=decision.verifier_call.tokens_out if decision.verifier_call else 0,
        ms=ms,
    )


def verifier_summary(call: VerifierCall | None) -> dict[str, Any] | None:
    """What a gate-decision row keeps of the C4 call (the full I/O is in ``verifier_evals``)."""
    if call is None:
        return None
    return {
        "variant": call.variant,
        "output": call.output.model_dump(mode="json") if call.output else None,
        "error": call.error,
        "prompt_hash": call.prompt_hash,
    }


# ---- Exp 1V and composition (T3.5) -------------------------------------------------------------


def judge(case: Exp1Case, verifier: LLMVerifier, counter: TokenCounter) -> VerifierCall:
    """Exp 1V: the verifier on the package exactly as C4 would see it, regardless of C1-C3."""
    con = open_case_db(case.db_path)
    try:
        cited = list(case.package.cited)
        by_id = read_cited(con, cited)
        records = [by_id[i] for i in dict.fromkeys(cited) if i in by_id]  # as in the gate
        evidence = render_cited(records, counter)
        prompt, retrieval = verifier.prompt(
            case.package.tool, case.package.call_args(), records, evidence, case.context,
            counter, claim_of(case),
        )  # fmt: skip
        return verifier.call(prompt, retrieval)
    finally:
        con.close()


_EVAL_SPLIT = {"manifest", "prompt_hash", "tokens_in", "tokens_out", "messages"}


def eval_row(run_id: int, case_id: str, run_idx: int, call: VerifierCall) -> VerifierEval:
    return VerifierEval(
        run_id=run_id,
        case_id=case_id,
        variant=call.variant,
        run_idx=run_idx,
        verdict=call.output.verdict if call.output else None,
        output=call.model_dump(mode="json", exclude=_EVAL_SPLIT),
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        manifest=call.manifest,
        prompt_hash=call.prompt_hash,
    )


def call_from_row(row: VerifierEval) -> VerifierCall:
    return VerifierCall.model_validate({
        **(row.output or {}), "manifest": row.manifest or {}, "prompt_hash": row.prompt_hash,
        "tokens_in": row.tokens_in, "tokens_out": row.tokens_out,
    })  # fmt: skip


@dataclass(frozen=True)
class StoredVerifier:
    """C4 for Exp 1G: returns the stored Exp 1V call; the model is not called again."""

    stored: VerifierCall

    def __call__(self, *_: Any, **__: Any) -> VerifierCall:
        return self.stored


def compose(
    case: Exp1Case, config: GateConfig, policy: PolicyEngine, stored: VerifierCall,
    counter: TokenCounter,
) -> tuple[GateDecision, float]:  # fmt: skip
    """Exp 1G for a configuration with C4: the code checks plus the stored verifier output."""
    if config.verifier_variant != stored.variant:
        raise ValueError(f"{config.id} uses variant {config.verifier_variant}, "
                         f"stored call is {stored.variant}")  # fmt: skip
    return decide(case, Gate(config, policy, StoredVerifier(stored)), counter)


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
    ctx = TrustedContext.model_validate(scenario.trusted_context)
    edit = REdit.model_validate(row.r_edit) if row.r_edit else None
    if edit is not None:  # Set R: the one edited field (tier, approval script or toolset)
        ctx = effective_context(ctx, str(scenario.target_host), edit)
    return Exp1Case(
        case_id=row.id,
        db_path=settings.resolve(Path(row.case_db_path)),
        context=ctx,
        toolset=tuple(effective_toolset(edit)),
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
