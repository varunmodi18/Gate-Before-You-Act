"""Preliminary sensitivity (plan §D.12, T7.3): the expected half-width of a paired, clustered
difference of proportions, and its inputs estimated from a pilot. Shown as "preliminary
sensitivity", never as power.

    half-width = z * sqrt(DE * d / n),   DE = 1 + (m - 1) * rho

with ``n`` cases, ``m`` cases per scenario, ``rho`` the within-scenario correlation of the paired
differences and ``d`` the share of discordant pairs (the variance of a paired 0/1 difference is
at most ``d``). With the proposal's assumptions (rho 0.2, d 0.30) this gives 0.107 for H1 (160
cases, 4 per scenario) and 0.131 for H1-C4 (80 cases, 2 per scenario), the proposal's 11 and 13
points; for H2 (120 cases, 3 per scenario) it gives 0.116, not the proposal's 8 points (recorded
in STATUS.md for the team).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

Z95 = 1.959963984540054


def design_effect(m: float, rho: float) -> float:
    return 1 + (m - 1) * rho


def expected_half_width(n_cases: int, cases_per_scenario: float, rho: float,
                        discordance: float, z: float = Z95) -> float:  # fmt: skip
    if n_cases <= 0:
        raise ValueError("n_cases must be positive")
    return z * math.sqrt(design_effect(cases_per_scenario, rho) * discordance / n_cases)


@dataclass(frozen=True)
class PilotEstimate:
    discordance: float  # share of cases where the two systems' indicators differ
    rho: float  # within-scenario correlation of the paired differences (ICC, ANOVA estimator)
    n_cases: int
    n_scenarios: int
    cases_per_scenario: float


def estimate(a: Mapping[str, float], b: Mapping[str, float],
             scenario_of: Mapping[str, str]) -> PilotEstimate:  # fmt: skip
    """Discordance and the one-way ANOVA intraclass correlation of ``a - b`` from paired per-case
    indicators (run-averaged values are allowed; a case is discordant when they differ)."""
    cases = sorted(set(a) & set(b))
    if not cases:
        raise ValueError("no paired cases")
    groups: dict[str, list[float]] = defaultdict(list)
    for c in cases:
        groups[scenario_of[c]].append(a[c] - b[c])
    k, n = len(groups), len(cases)
    disc = sum(a[c] != b[c] for c in cases) / n
    grand = sum(sum(v) for v in groups.values()) / n
    ssb = sum(len(v) * (sum(v) / len(v) - grand) ** 2 for v in groups.values())
    ssw = sum((x - sum(v) / len(v)) ** 2 for v in groups.values() for x in v)
    m0 = (n - sum(len(v) ** 2 for v in groups.values()) / n) / (k - 1) if k > 1 else 0.0
    if k < 2 or n - k <= 0 or m0 <= 0:
        rho = 0.0
    else:
        msb, msw = ssb / (k - 1), ssw / (n - k)
        denom = msb + (m0 - 1) * msw
        rho = max(0.0, (msb - msw) / denom) if denom > 0 else 0.0
    return PilotEstimate(disc, rho, n, k, n / k)


# The full design (proposal §11): 40 test scenarios.
DESIGN = {"H1": (160, 4), "H1_C4": (80, 2), "H2": (120, 3)}  # (cases, cases per scenario)
PROPOSAL = {"rho": 0.2, "discordance": 0.3}


def pilot_report(rows1: list[Any], eps: list[Any]) -> dict[str, Any]:
    """Preliminary sensitivity from a pilot (an early partial run on dev scenarios): per
    hypothesis, the estimated discordance and within-scenario correlation, and the expected
    half-width at the full design size with those estimates and with the proposal's assumptions.
    Never power; never a verdict."""
    from gbya.analysis import hypotheses as hy
    from gbya.scoring.metrics import MetricRefused

    Getter = Callable[[], tuple[dict[str, float], dict[str, float], list[Any]]]
    pairs: dict[str, Getter] = {
        "H1": lambda: (hy.admitted_on_reject(rows1, "G1", hy.H1_VARIANTS),
                       hy.admitted_on_reject(rows1, "G3", hy.H1_VARIANTS), rows1),
        "H1_C4": lambda: (hy.admitted_on_reject(rows1, "A1", hy.H1_C4_VARIANTS),
                          hy.admitted_on_reject(rows1, "G3", hy.H1_C4_VARIANTS), rows1),
        "H2": lambda: (hy.deferral_on_justified(eps, "G3"), hy.deferral_on_justified(eps, "G1"),
                       eps),
    }  # fmt: skip
    out: dict[str, Any] = {"preliminary_sensitivity": True, "assumed": PROPOSAL}
    for name, get in pairs.items():
        n, m = DESIGN[name]
        try:
            a, b, rows = get()
            est = estimate(a, b, {r.case_id: r.scenario_id for r in rows})
        except (MetricRefused, ValueError) as exc:
            out[name] = {"refused": str(exc)}
            continue
        out[name] = {
            "pilot": {"discordance": est.discordance, "rho": est.rho, "n_cases": est.n_cases,
                      "n_scenarios": est.n_scenarios},
            "design": {"n_cases": n, "cases_per_scenario": m},
            "half_width": expected_half_width(n, m, est.rho, est.discordance),
            "half_width_assumed": expected_half_width(n, m, PROPOSAL["rho"],
                                                      PROPOSAL["discordance"]),
        }  # fmt: skip
        out[name]["half_width_pp"] = round(out[name]["half_width"] * 100, 1)
        out[name]["half_width_assumed_pp"] = round(out[name]["half_width_assumed"] * 100, 1)
    return out
