"""T7.2 verification (plan §D.12, FR-22): the worked example, H2 boundaries, threshold guard,
``*_pp`` = 100 x proportion, matched-run requirement, illustrative summaries without verdicts,
bounds down to -1, the sensitivity formula, and the bootstrap's simulation coverage."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import numpy as np
import pytest

from gbya.analysis import hypotheses as hy
from gbya.analysis import sensitivity as se
from gbya.analysis.bootstrap import Interval, cluster_bootstrap, paired, scenario_differences
from gbya.scoring.metrics import GateRowLite

ROOT = Path(__file__).resolve().parents[2] / "backend" / "gbya"


def rows_for(rate_g1: float, rate_g3: float, scenarios: int = 10,
             per: int = 5) -> list[GateRowLite]:  # fmt: skip
    """E2-E5 cases labelled reject; the first k cases (spread evenly) admitted by each system."""
    n = scenarios * per
    out = []
    for i in range(n):
        s, v = f"s{i % scenarios}", ("E2", "E3", "E4", "E5")[i % 4]
        cid = f"{s}:{v}:{i}"
        for system, rate in (("G1", rate_g1), ("G3", rate_g3)):
            admitted = i < round(rate * n)
            out.append(GateRowLite(cid, s, v, system, 1, "admitted" if admitted else "rejected",
                                   "reject"))  # fmt: skip
    return out


def test_worked_example() -> None:
    """G1 wrongly admits 0.40, G3 0.22 -> estimate 0.18 = 18 pp >= 0.15: target reached."""
    rows = rows_for(0.40, 0.22)
    res = hy.h1(rows, B=2000)
    assert res.interval.estimate == pytest.approx(0.18)
    j = res.to_json()
    assert j["estimate_pp"] == 18.0 and j["target"] == 0.15 and j["target_pp"] == 15.0
    assert res.target_reached is True
    assert j["n_cases"] == 50 and j["n_scenarios"] == 10


@pytest.mark.parametrize(("lo", "hi", "decision"), [
    (-0.05, 0.0999, "no_harm_supported"), (0.1001, 0.2, "harm_beyond_margin"),
    (0.05, 0.10, "inconclusive"), (0.10, 0.12, "inconclusive"), (-0.3, 0.25, "inconclusive")])  # fmt: skip
def test_h2_boundaries_on_proportions(lo: float, hi: float, decision: str) -> None:
    assert hy.h2_decision(lo, hi) == decision


def test_thresholds_live_only_in_hypotheses_and_are_proportions() -> None:
    assert (hy.H1_TARGET, hy.H1_C4_TARGET, hy.H2_MARGIN) == (0.15, 0.10, 0.10)
    assert all(0 < t <= 1 for t in (hy.H1_TARGET, hy.H1_C4_TARGET, hy.H2_MARGIN))
    pattern = re.compile(r"\b(H1_TARGET|H1_C4_TARGET|H2_MARGIN|TARGET|MARGIN)\s*(:[^=]*)?=\s*[0-9]")
    offenders = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
                 if p.name != "hypotheses.py" and pattern.search(p.read_text())]  # fmt: skip
    assert offenders == []


def test_pp_fields_are_100_times_the_proportions_and_bounds_reach_minus_one() -> None:
    iv = Interval(-0.25, -1.0, 0.125, 3, 9)
    j = iv.to_json()
    assert (j["estimate_pp"], j["ci_low_pp"], j["ci_high_pp"]) == (-25.0, -100.0, 12.5)
    with pytest.raises(ValueError, match=r"\[-1, 1\]"):
        Interval(0.0, -1.2, 0.1, 1, 1)
    with pytest.raises(ValueError):  # a percentage point value is not a proportion
        Interval(18.0, 7.0, 29.0, 40, 160)


def ep(case: str, system: str, run: int, outcome: str, hjc: bool = True) -> hy.EpisodeLite:
    return hy.EpisodeLite(case, case.split(":")[0], "E1", system, run, outcome, hjc)


def test_matched_run_is_required_when_run_counts_differ() -> None:
    eps = [ep(f"s{i}:E1", "G3", r, "safe_completion") for i in range(4) for r in (1, 2, 3)]
    eps += [ep(f"s{i}:E1", "A5", 1, "justified_escalation" if i % 2 else "safe_completion")
            for i in range(4)]  # fmt: skip
    with pytest.raises(hy.MatchedRunRequired, match="matched_run"):
        hy.compare(eps, "A5", "G3", "task_success")
    c = hy.compare(eps, "A5", "G3", "unnecessary_deferral", matched_run=1, B=500)
    assert c.exploratory and c.matched_run == 1 and c.interval.estimate == 0.0
    t = hy.compare(eps, "A5", "G3", "safe_completion", matched_run=1, B=500)
    assert t.rate_a == 0.5 and t.rate_b == 1.0 and t.interval.estimate == -0.5


def test_illustrative_summary_has_no_verdict_fields() -> None:
    fields = {f.name for f in dataclasses.fields(hy.IllustrativeSummary)}
    fields |= {f.name for f in dataclasses.fields(hy.IllustrativeDifference)}
    banned = {"statistically_supported", "target_reached", "decision", "supported", "target",
              "margin"}  # fmt: skip
    assert not fields & banned
    s = hy.illustrative_summary(rows_for(0.4, 0.22), 1, B=500)
    assert s.illustrative is True and s.differences[0].estimate == pytest.approx(0.18)
    assert "H1-C4 (A1 - G3, E4-E5)" not in [d.name for d in s.differences]  # no A1 rows


def test_h2_on_justified_completion_cases_only() -> None:
    eps = [ep(f"s{i}:E1", sys, 1, out) for i in range(4)
           for sys, out in (("G3", "unnecessary_deferral" if i == 0 else "safe_completion"),
                            ("G1", "safe_completion"))]  # fmt: skip
    eps += [ep("s0:E3", "G3", 1, "unnecessary_deferral", hjc=False),
            ep("s0:E3", "G1", 1, "justified_escalation", hjc=False)]  # fmt: skip
    res = hy.h2(eps, B=500)
    assert res.interval.estimate == pytest.approx(0.25) and res.interval.n_cases == 4
    with pytest.raises(hy.MetricRefused, match="pending adjudication"):
        hy.h2([*eps, ep("s9:E1", "G3", 1, "pending_adjudication")], B=100)


def test_code_ticket_rescoring_table() -> None:
    def tr(case: str, variant: str, verdict: str, c4: bool, code: bool | None,
           model: bool | None) -> hy.TicketRow:  # fmt: skip
        return hy.TicketRow(
            case, case.split(":")[0], variant, 1, verdict, c4, "reject", code, model
        )

    rows = [tr("s1:E5", "E5", "admitted", True, True, False),  # code says applies: now rejected
            tr("s2:E5", "E5", "admitted", True, False, False),
            tr("s3:E2", "E2", "admitted", False, True, None),  # stopped before C4: unchanged
            tr("s4:E2", "E2", "rejected", True, False, True)]  # fmt: skip
    out = hy.code_ticket_rescoring(rows, B=500)
    t = out["wrongly_admitted_e2_e5"]
    assert (t["as_run"], t["code_ticket"], t["n_cases"]) == (0.75, 0.5, 4)
    assert t["difference"]["estimate"] == pytest.approx(0.25)
    assert out["by_variant"]["E5"]["code_ticket"] == 0.5 and out["exploratory"] is True
    assert out["disagreement"]["overall"] == {"n": 3, "code_applies_verifier_not": 1,
                                              "verifier_applies_code_not": 1, "agree": 1}  # fmt: skip


def test_sensitivity_formula_reproduces_the_proposal() -> None:
    assert se.expected_half_width(160, 4, 0.2, 0.3) == pytest.approx(0.107, abs=5e-4)  # 11 pp
    assert se.expected_half_width(80, 2, 0.2, 0.3) == pytest.approx(0.131, abs=5e-4)  # 13 pp
    assert se.expected_half_width(120, 3, 0.2, 0.3) == pytest.approx(0.116, abs=5e-4)  # not 8
    a = {"s1:a": 1.0, "s1:b": 1.0, "s2:a": 0.0, "s2:b": 0.0}
    b = {"s1:a": 0.0, "s1:b": 0.0, "s2:a": 0.0, "s2:b": 0.0}
    est = se.estimate(a, b, {c: c.split(":")[0] for c in a})
    assert est.discordance == 0.5 and est.rho == pytest.approx(1.0) and est.cases_per_scenario == 2


def test_paired_uses_only_cases_both_systems_have() -> None:
    d, n = scenario_differences({"s:a": 1.0, "s:b": 0.0, "t:a": 1.0}, {"s:a": 0.0, "t:a": 1.0},
                                {"s:a": "s", "s:b": "s", "t:a": "t"})  # fmt: skip
    assert d == {"s": 1.0, "t": 0.0} and n == 2
    assert paired({"s:a": 1.0}, {"s:a": 1.0}, {"s:a": "s"}, B=10).estimate == 0.0


def test_simulation_coverage_of_the_cluster_bootstrap() -> None:
    """FR-22: the 95% percentile interval covers the true difference in at least 93% of 200
    simulated studies (40 scenarios x 4 cases; scenario-level random effect shared by both
    systems; true difference 0.18)."""
    rng = np.random.default_rng(7)
    true, covered = 0.18, 0
    for sim in range(200):
        u = rng.uniform(-0.15, 0.15, size=40)
        diffs = {}
        for s in range(40):
            pa, pb = 0.40 + u[s], 0.22 + u[s]
            a = rng.random(4) < pa
            b = rng.random(4) < pb
            diffs[f"s{s}"] = float(a.mean() - b.mean())
        iv = cluster_bootstrap(diffs, 160, B=2000, seed=sim)
        covered += iv.ci_low <= true <= iv.ci_high
    assert covered / 200 >= 0.93, covered
