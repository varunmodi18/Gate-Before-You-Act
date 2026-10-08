"""Inter-annotator agreement before adjudication (plan T4.8, A-11, §D.9).

* **Cohen's κ** on two label types (A-11): the per-case ``correct_outcome``, and the
  per-candidate action label: every action either annotator listed as permitted or prohibited
  for a case, labelled ``permitted`` / ``prohibited`` / ``unlisted`` by each annotator.
* **Jaccard** on ``evidence_counterfactual`` of the scenario's E1 case.

A snapshot is stored per scenario when both annotators have submitted, before adjudication; the
report pools the snapshots' items (κ needs many items; a single scenario has seven cases).
κ is None when expected agreement is 1 (no variation): the value is undefined, not perfect.
Proportions only.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.store.models import AgreementSnapshot, Annotation, Scenario, utcnow


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def jaccard(a: Iterable[int], b: Iterable[int]) -> float | None:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return None
    return len(sa & sb) / len(sa | sb)


def _action_key(entry: dict[str, Any]) -> str:
    return json.dumps({"tool": entry.get("tool"), "args": entry.get("args", {})}, sort_keys=True)


def _action_labels(labels: dict[str, Any]) -> dict[str, str]:
    out = {_action_key(e): "prohibited" for e in labels.get("prohibited", [])}
    out |= {_action_key(e): "permitted" for e in labels.get("permitted", [])}
    return out


def items(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Paired items from two annotations' labels (variant → labels)."""
    outcome, actions = [], []
    for variant in sorted(set(a) | set(b)):
        la, lb = a.get(variant, {}), b.get(variant, {})
        outcome.append([variant, la.get("correct_outcome"), lb.get("correct_outcome")])
        xa, xb = _action_labels(la), _action_labels(lb)
        for key in sorted(set(xa) | set(xb)):
            actions.append([variant, key, xa.get(key, "unlisted"), xb.get(key, "unlisted")])
    e1a = a.get("E1", {}).get("evidence_counterfactual", [])
    e1b = b.get("E1", {}).get("evidence_counterfactual", [])
    return {
        "outcome": outcome,
        "actions": actions,
        "jaccard_e1": jaccard(e1a, e1b),
        "evidence_e1": [e1a, e1b],
    }


def summarise(snapshot_items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    outcome = [(str(x[1]), str(x[2])) for s in snapshot_items for x in s["outcome"]]
    actions = [(str(x[2]), str(x[3])) for s in snapshot_items for x in s["actions"]]
    jac = [s["jaccard_e1"] for s in snapshot_items if s.get("jaccard_e1") is not None]
    return {
        "scenarios": len(snapshot_items),
        "kappa_outcome": cohen_kappa(outcome),
        "outcome_items": len(outcome),
        "outcome_agreement": (sum(a == b for a, b in outcome) / len(outcome)) if outcome else None,
        "kappa_actions": cohen_kappa(actions),
        "action_items": len(actions),
        "action_agreement": (sum(a == b for a, b in actions) / len(actions)) if actions else None,
        "jaccard_e1_mean": (sum(jac) / len(jac)) if jac else None,
        "jaccard_n": len(jac),
    }


def store_snapshot(session: Session, sid: str) -> AgreementSnapshot:
    """Called when the second annotator submits; never recomputed after adjudication."""
    a = session.scalar(
        select(Annotation).where(Annotation.scenario_id == sid, Annotation.annotator_role == "A")
    )
    b = session.scalar(
        select(Annotation).where(Annotation.scenario_id == sid, Annotation.annotator_role == "B")
    )
    if a is None or b is None:
        raise ValueError(f"{sid}: both annotations are needed")
    snap = session.scalar(select(AgreementSnapshot).where(AgreementSnapshot.scenario_id == sid))
    if snap is not None:
        return snap
    values = items(a.labels, b.labels)
    values["summary"] = summarise([values])
    snap = AgreementSnapshot(scenario_id=sid, computed_at=utcnow(), values=values)
    session.add(snap)
    session.flush()
    return snap


def report(session: Session, split: str | None = None) -> dict[str, Any]:
    q = select(AgreementSnapshot, Scenario.split).join(
        Scenario, Scenario.id == AgreementSnapshot.scenario_id
    )
    rows = [(s, sp) for s, sp in session.execute(q) if split is None or sp == split]
    return {
        "split": split,
        "pooled": summarise([s.values for s, _ in rows]),
        "per_scenario": [
            {"scenario_id": s.scenario_id, "split": sp, **s.values["summary"]} for s, sp in rows
        ],
        "note": "Computed before adjudication (snapshot at the second submission). Proportions; "
        "κ is undefined (null) when there is no variation.",
    }
