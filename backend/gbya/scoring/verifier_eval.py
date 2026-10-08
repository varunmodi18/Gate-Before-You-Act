"""Exp 1V diagnostic verifier accuracy and Exp 1G C4 invocation counts (plan §D.7.2, T3.5).

Deterministic counting over stored rows, no LLM judge (§L.4 item 6). Rates are proportions in
[0, 1] (§L.4 item 12). The two views are never mixed: the diagnostic accuracy is over **every**
package; the invocation counts and the conditional accuracy are over the packages a gate
configuration actually sent to C4.

* A row whose output did not parse has the predicted class ``PARSE_ERROR``: always wrong in exact
  accuracy; in binary accuracy (SUPPORTS vs not) it counts as "not SUPPORTS", which is how the gate
  treats it (``C4_PARSE_ERROR`` rejects).
* A case without ``labels.verifier_label`` is left out of the accuracy and counted as unlabelled.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

VERDICTS = ("SUPPORTS", "INSUFFICIENT", "CONTRADICTED")
PARSE_ERROR = "PARSE_ERROR"


@dataclass(frozen=True)
class EvalRow:
    case_id: str
    variant: str  # verifier variant
    run_idx: int
    verdict: str | None  # None: output did not parse


@dataclass(frozen=True)
class GateRow:
    case_id: str
    system: str
    run_idx: int
    reached_c4: bool
    c4_verdict: str | None  # None if not reached or unparseable


@dataclass(frozen=True)
class CaseLabel:
    variant: str  # case variant (E1..E5, R_pos, R_neg)
    verifier_label: str | None


def _rate(k: int, n: int) -> float | None:
    return k / n if n else None


def _accuracy(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    n = len(pairs)
    exact = sum(1 for label, pred in pairs if label == pred)
    binary = sum(1 for label, pred in pairs if (label == "SUPPORTS") == (pred == "SUPPORTS"))
    return {"n": n, "exact_correct": exact, "binary_correct": binary,
            "exact_accuracy": _rate(exact, n), "binary_accuracy": _rate(binary, n)}  # fmt: skip


def verifier_report(
    evals: Iterable[EvalRow], gates: Iterable[GateRow], labels: Mapping[str, CaseLabel]
) -> dict[str, Any]:
    by_variant: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
    unlabelled: set[str] = set()
    parse_errors: Counter[str] = Counter()
    for e in evals:
        lab = labels.get(e.case_id)
        if lab is None or lab.verifier_label is None:
            unlabelled.add(e.case_id)
            continue
        pred = e.verdict or PARSE_ERROR
        if e.verdict is None:
            parse_errors[e.variant] += 1
        by_variant[e.variant].append((lab.verifier_label, pred, lab.variant, e.run_idx))

    variants: dict[str, Any] = {}
    for variant, rows in sorted(by_variant.items()):
        confusion = {lab: dict.fromkeys((*VERDICTS, PARSE_ERROR), 0) for lab in VERDICTS}
        for label, pred, _, _ in rows:
            confusion.setdefault(label, dict.fromkeys((*VERDICTS, PARSE_ERROR), 0))[pred] += 1
        by_case_variant = {
            cv: _accuracy([(lab, p) for lab, p, v, _ in rows if v == cv])
            for cv in sorted({r[2] for r in rows})
        }
        by_run = {
            str(r): _accuracy([(lab, p) for lab, p, _, ri in rows if ri == r])
            for r in sorted({r[3] for r in rows})
        }
        variants[variant] = {
            **_accuracy([(lab, p) for lab, p, _, _ in rows]),
            "parse_errors": parse_errors[variant],
            "confusion": confusion,  # label → predicted → count
            "by_case_variant": by_case_variant,
            "by_run": by_run,
        }

    invocations: dict[str, Any] = {}
    gate_rows: dict[str, list[GateRow]] = defaultdict(list)
    for g in gates:
        gate_rows[g.system].append(g)
    for system, rows_g in sorted(gate_rows.items()):
        per_cv: dict[str, dict[str, Any]] = {}
        reached_pairs: list[tuple[str, str]] = []
        for cv in sorted({labels[g.case_id].variant for g in rows_g if g.case_id in labels}):
            sub = [g for g in rows_g if g.case_id in labels and labels[g.case_id].variant == cv]
            reached = [g for g in sub if g.reached_c4]
            pairs = [(str(labels[g.case_id].verifier_label), g.c4_verdict or PARSE_ERROR)
                     for g in reached if labels[g.case_id].verifier_label]  # fmt: skip
            reached_pairs += pairs
            per_cv[cv] = {"packages": len(sub), "reached_c4": len(reached),
                          "conditional": _accuracy(pairs)}  # fmt: skip
        invocations[system] = {
            "packages": len(rows_g),
            "reached_c4": sum(1 for g in rows_g if g.reached_c4),
            "conditional": _accuracy(reached_pairs),
            "by_case_variant": per_cv,
        }
    return {
        "diagnostic": variants,
        "gate_path": invocations,
        "unlabelled_cases": sorted(unlabelled),
        "note": "Diagnostic accuracy is over every package; gate-path numbers only over packages "
        "that reached C4. Rates are proportions.",
    }
