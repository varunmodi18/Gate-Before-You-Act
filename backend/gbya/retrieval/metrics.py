"""Retrieval metrics over stored rankings (plan §D.3 "Retrieval metrics", T3.7).

Gold rule set G from ``gold.GoldMap`` (technique tags; scoring only). Binary relevance.

* Recall@5 = |top5 ∩ G| / |G| (20 gold rules, 5 retrieved and all relevant → 0.25);
* Hit@5 = 1 if top5 ∩ G is non-empty;
* nDCG@5 with ideal DCG over min(5, |G|) relevant items;
* MRR@20 = 1 / rank of the first gold rule in the stored top 20, 0 if none;
* ATT&CK top-1 = the retrieved technique is ``technique_gold`` or its parent.

Cases with an empty G are excluded from the Sigma metrics and counted; the report also gives the
mean |G| and the mean Recall@5 ceiling min(1, 5 / |G|). Proportions only (§L.4 item 12).
Technique tags are proxy relevance labels: these numbers measure technique-level retrieval, not
event-level relevance (stated limitation).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

from gbya.retrieval.gold import GoldMap

K = 5
MRR_DEPTH = 20


def recall_at_k(ranking: Sequence[str], gold: set[str], k: int = K) -> float:
    return len(set(ranking[:k]) & gold) / len(gold)


def hit_at_k(ranking: Sequence[str], gold: set[str], k: int = K) -> float:
    return 1.0 if set(ranking[:k]) & gold else 0.0


def ndcg_at_k(ranking: Sequence[str], gold: set[str], k: int = K) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranking[:k]) if d in gold)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(gold))))
    return dcg / ideal


def mrr_at_k(ranking: Sequence[str], gold: set[str], k: int = MRR_DEPTH) -> float:
    for i, d in enumerate(ranking[:k]):
        if d in gold:
            return 1 / (i + 1)
    return 0.0


@dataclass(frozen=True)
class RankedCase:
    case_id: str
    case_variant: str
    technique_gold: str | None
    mode: str
    sigma: list[str]  # stored Sigma ranking (doc ids, best first, up to 20)
    attack_top1: str | None


def case_metrics(c: RankedCase, gold: GoldMap) -> dict[str, Any]:
    """Per-case values; Sigma metrics are None when the gold set is empty."""
    g = gold.gold_rules(c.technique_gold) if c.technique_gold else set()
    out: dict[str, Any] = {"gold_size": len(g), "attack_top1": None}
    if c.technique_gold:
        out["attack_top1"] = 1.0 if gold.attack_correct(c.attack_top1, c.technique_gold) else 0.0
    if g:
        out |= {"recall_at_5": recall_at_k(c.sigma, g), "hit_at_5": hit_at_k(c.sigma, g),
                "ndcg_at_5": ndcg_at_k(c.sigma, g), "mrr_at_20": mrr_at_k(c.sigma, g),
                "recall_ceiling": min(1.0, K / len(g))}  # fmt: skip
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sigma = [r for r in rows if r["gold_size"] > 0]
    attack = [r["attack_top1"] for r in rows if r["attack_top1"] is not None]

    def avg(key: str) -> float | None:
        return mean(r[key] for r in sigma) if sigma else None

    return {
        "cases": len(rows),
        "sigma_n": len(sigma),
        "excluded_empty_gold": len(rows) - len(sigma),
        "recall_at_5": avg("recall_at_5"),
        "hit_at_5": avg("hit_at_5"),
        "ndcg_at_5": avg("ndcg_at_5"),
        "mrr_at_20": avg("mrr_at_20"),
        "mean_gold_size": mean(r["gold_size"] for r in sigma) if sigma else None,
        "mean_recall_ceiling": avg("recall_ceiling"),
        "attack_n": len(attack),
        "attack_top1": mean(attack) if attack else None,
    }


def retrieval_report(cases: Iterable[RankedCase], gold: GoldMap) -> dict[str, Any]:
    """Per mode: overall and by case variant."""
    by_mode: dict[str, list[tuple[RankedCase, dict[str, Any]]]] = defaultdict(list)
    for c in cases:
        by_mode[c.mode].append((c, case_metrics(c, gold)))
    report: dict[str, Any] = {}
    for mode, rows in sorted(by_mode.items()):
        variants = sorted({c.case_variant for c, _ in rows})
        report[mode] = {
            "overall": _summary([m for _, m in rows]),
            "by_case_variant": {
                v: _summary([m for c, m in rows if c.case_variant == v]) for v in variants
            },
        }
    return report
