"""Paired cluster bootstrap over scenarios (plan §D.12, FR-22, T7.2).

* Per system, each case's 0/1 indicator is averaged over runs (``metrics.per_case_mean``).
* Per scenario, the paired difference is the mean over the hypothesis's case subset of
  ``a(case) - b(case)``, on the cases both systems have (``scenario_differences``).
* The estimate is the mean of the per-scenario differences; scenarios are resampled with
  replacement (B = 10,000, seed 2026) and the 95% interval is the percentile interval.

Everything is a proportion: rates in [0, 1], differences and bounds in [-1, 1]. ``*_pp`` fields are
display only (100 x the proportion).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

B_DEFAULT = 10_000
SEED_DEFAULT = 2026
ALPHA = 0.05


def pp(x: float | None) -> float | None:
    """Display value in percentage points (never compared with a threshold)."""
    return None if x is None else round(x * 100, 1)


@dataclass(frozen=True)
class Interval:
    estimate: float
    ci_low: float
    ci_high: float
    n_scenarios: int
    n_cases: int

    def __post_init__(self) -> None:
        for name in ("estimate", "ci_low", "ci_high"):
            v = getattr(self, name)
            if not -1.0 <= v <= 1.0:
                raise ValueError(f"{name}={v} is not a proportion difference in [-1, 1]")

    def to_json(self) -> dict[str, Any]:
        return {"estimate": self.estimate, "ci_low": self.ci_low, "ci_high": self.ci_high,
                "estimate_pp": pp(self.estimate), "ci_low_pp": pp(self.ci_low),
                "ci_high_pp": pp(self.ci_high), "n_scenarios": self.n_scenarios,
                "n_cases": self.n_cases}  # fmt: skip


def scenario_differences(
    a: Mapping[str, float], b: Mapping[str, float], scenario_of: Mapping[str, str]
) -> tuple[dict[str, float], int]:
    """Per-scenario mean of ``a - b`` over the cases both have; also the number of cases."""
    per: dict[str, list[float]] = defaultdict(list)
    for case in sorted(set(a) & set(b)):
        per[scenario_of[case]].append(a[case] - b[case])
    return {s: sum(v) / len(v) for s, v in sorted(per.items())}, sum(len(v) for v in per.values())


def cluster_bootstrap(diffs: Mapping[str, float], n_cases: int, *, B: int = B_DEFAULT,
                      seed: int = SEED_DEFAULT) -> Interval:  # fmt: skip
    """Percentile interval of the mean per-scenario difference, resampling scenarios."""
    d = np.array([diffs[s] for s in sorted(diffs)], dtype=float)
    if d.size == 0:
        raise ValueError("no scenario has cases for both systems")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(B, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [ALPHA / 2, 1 - ALPHA / 2])
    return Interval(float(d.mean()), float(lo), float(hi), int(d.size), n_cases)


def paired(a: Mapping[str, float], b: Mapping[str, float], scenario_of: Mapping[str, str], *,
           B: int = B_DEFAULT, seed: int = SEED_DEFAULT) -> Interval:  # fmt: skip
    diffs, n = scenario_differences(a, b, scenario_of)
    return cluster_bootstrap(diffs, n, B=B, seed=seed)
