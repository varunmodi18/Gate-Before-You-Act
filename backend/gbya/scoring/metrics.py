"""Episode metrics (plan §D.9, FR-21; T5.7 task success rate, the rest with T7.1).

Rates are proportions in [0, 1]; percentage points appear only in ``*_pp`` display fields (§L.4
item 12).

Exp 3 (T6.2): technique precision/recall/F1 against the window's OTRF techniques plus adjudicated
incidental techniques (A-15), and cited-evidence precision/recall (§D.9).

**Task success rate** (proposal v4): the share of episodes whose final outcome is
``safe_completion`` or ``justified_escalation``. Because a justified escalation needs a qualifying
escalation (§D.9 step B), escalating indiscriminately does not raise it. Like the analysis, it
refuses episodes still ``pending_adjudication`` and episodes not yet scored (labels pending).
Single-run comparisons (A5 / A7 / G3) use run 1 only (``run_idx=1``, §D.6.4).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.store.models import Episode

SUCCESS = frozenset({"safe_completion", "justified_escalation"})


class MetricRefused(ValueError):
    """The episodes cannot be measured yet (pending adjudication or unscored)."""


@dataclass(frozen=True)
class Rate:
    successes: int
    n: int

    @property
    def rate(self) -> float | None:
        return self.successes / self.n if self.n else None

    @property
    def rate_pp(self) -> float | None:  # display only
        return None if self.rate is None else round(self.rate * 100, 1)


def task_success_rate(outcomes: Iterable[str | None]) -> Rate:
    values = list(outcomes)
    pending = sum(o == "pending_adjudication" for o in values)
    if pending:
        raise MetricRefused(f"{pending} episode(s) are pending adjudication")
    unscored = sum(o is None for o in values)
    if unscored:
        raise MetricRefused(f"{unscored} episode(s) are not scored (labels pending)")
    return Rate(sum(o in SUCCESS for o in values), len(values))


def task_success_by_system(
    rows: Iterable[tuple[str, int, str | None]], run_idx: int | None = None
) -> dict[str, Rate]:
    """``rows`` are (system, run_idx, outcome); ``run_idx`` restricts to one run (matched run)."""
    grouped: dict[str, list[str | None]] = defaultdict(list)
    for system, idx, outcome in rows:
        if run_idx is None or idx == run_idx:
            grouped[system].append(outcome)
    return {s: task_success_rate(v) for s, v in sorted(grouped.items())}


def run_task_success(session: Session, run_id: int, run_idx: int | None = None
                     ) -> dict[str, Rate]:  # fmt: skip
    rows = session.execute(
        select(Episode.system, Episode.run_idx, Episode.outcome).where(
            Episode.run_id == run_id, Episode.status == "done"
        )
    ).all()
    return task_success_by_system(((s, int(i), o) for s, i, o in rows), run_idx)


# ---- Exp 3: technique F1 and cited-evidence precision/recall (T6.2) ------------------------------


def _parent(technique: str) -> str:
    return technique.split(".", 1)[0]


def technique_match(predicted: str, gold: str) -> bool:
    """Equal IDs, or one is a technique-level ID and the other its sub-technique (as the gold
    rule set of §D.3 treats a technique-level ``technique_gold``)."""
    if predicted == gold:
        return True
    return ("." not in predicted or "." not in gold) and _parent(predicted) == _parent(gold)


@dataclass(frozen=True)
class TechniqueScore:
    tp: int
    fp: int
    fn: int
    pending: tuple[str, ...]  # predicted, not in the metadata, not yet adjudicated (A-15)

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None:
            return None
        return 2 * p * r / (p + r) if p + r else 0.0


def technique_score(predicted: Iterable[str], gold: Iterable[str],
                    incidental: Mapping[str, bool] | None = None) -> TechniqueScore:  # fmt: skip
    """Exp 3 technique scores of one episode. Ground truth = the window's OTRF techniques plus
    predicted techniques adjudicated as incidental (A-15); a predicted technique outside the
    metadata and not yet adjudicated is ``pending`` (and counted nowhere)."""
    incidental = incidental or {}
    pred = sorted(set(predicted))
    truth = set(gold) | {t for t, ok in incidental.items() if ok}
    tp = fp = 0
    pending = []
    for p in pred:
        if any(technique_match(p, g) for g in truth):
            tp += 1
        elif p in incidental:  # adjudicated as not incidental: a false positive
            fp += 1
        else:
            pending.append(p)
    fn = sum(1 for g in truth if not any(technique_match(p, g) for p in pred))
    return TechniqueScore(tp, fp, fn, tuple(pending))


def micro_f1(scores: Iterable[TechniqueScore]) -> TechniqueScore:
    """Pooled counts over episodes; refused while an incidental technique is pending."""
    scores = list(scores)
    pending = sorted({p for s in scores for p in s.pending})
    if pending:
        raise MetricRefused(f"incidental techniques pending adjudication: {pending}")
    return TechniqueScore(sum(s.tp for s in scores), sum(s.fp for s in scores),
                          sum(s.fn for s in scores), ())  # fmt: skip


@dataclass(frozen=True)
class EvidenceScore:
    precision: float | None
    recall: float | None
    cited: int
    relevant: int


def evidence_score(cited: Iterable[int], retrievable: Iterable[int],
                   has_justified_completion: bool | None) -> EvidenceScore | None:  # fmt: skip
    """Cited-evidence precision and recall against ``evidence_retrievable`` (§D.9), only for
    cases with a justified completion; None (n/a) otherwise."""
    if not has_justified_completion:
        return None
    c, r = set(cited), set(retrievable)
    hit = len(c & r)
    return EvidenceScore(hit / len(c) if c else None, hit / len(r) if r else None, len(c), len(r))
