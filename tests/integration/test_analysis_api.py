"""T7.2: the analysis API on a hand-built research dataset (4 scenarios), with refusals for
non-research, replay and pending data, illustrative mode without verdicts, matched-run
comparisons and the exploratory code-ticket table. The rows are synthetic test data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gbya.api.main import create_app
from gbya.config import Settings
from gbya.store import db
from gbya.store.models import Case, Episode, GateDecisionRow, Run, Scenario, VerifierEval
from tests.integration.test_builder import WID, make_env

API = "/api/v1/analysis"
SCEN = ["s0", "s1", "s2", "s3"]


def _gate(run: int, case: str, system: str, idx: int, admitted: bool) -> GateDecisionRow:
    checks = [{"check": "C1", "passed": True, "code": "OK"}]
    if system == "G3":
        checks.append({"check": "C4", "passed": admitted, "code": "OK" if admitted else "C4"})
    return GateDecisionRow(run_id=run, case_id=case, system=system, run_idx=idx, call={},
                           cited=[], checks=checks, verdict="admitted" if admitted else "rejected")  # fmt: skip


def build(tmp: Path, purpose: str = "research", replay: bool = False) -> tuple[Settings, int, int]:
    s = make_env(tmp)
    (s.cases_dir).mkdir(parents=True, exist_ok=True)
    (s.cases_dir / "FROZEN.json").write_text(json.dumps({"case_set_hash": "h"}))
    factory = db.make_sessionmaker(db.make_engine(s.app_db_path))
    with db.session_scope(factory) as ses:
        r1 = Run(experiment=1, purpose=purpose, config={"spec": {"schedule": {"1": {}}}},
                 config_hash="c1", case_set_hash="h" if purpose == "research" else None,
                 replay=replay, status="completed", git_sha="abc", model_id="m")  # fmt: skip
        r2 = Run(experiment=2, purpose=purpose, config={}, config_hash="c2",
                 case_set_hash="h" if purpose == "research" else None, replay=replay,
                 status="completed")  # fmt: skip
        ses.add_all([r1, r2])
        ses.flush()
        for i, sid in enumerate(SCEN):
            ses.add(Scenario(id=sid, window_id=WID, split="test"))
            ses.flush()
            for v in ("E1", "E2", "E3", "E4", "E5"):
                label = "admit" if v == "E1" else "reject"
                ses.add(Case(id=f"{sid}:{v}", scenario_id=sid, set_="E", variant=v, request={},
                             package={}, labels={"initial_gate_label": label,
                                                 "has_justified_completion": v == "E1"}))  # fmt: skip
            ses.flush()
            for v in ("E2", "E3", "E4", "E5"):
                cid = f"{sid}:{v}"
                ses.add(_gate(r1.id, cid, "G1", 1, i < 2 or v in ("E2", "E3")))  # 1, 1, .5, .5
                ses.add(_gate(r1.id, cid, "A1", 1, True))
                for idx in (1, 2, 3):  # G3 admits only s0:E2 (in every run)
                    ses.add(_gate(r1.id, cid, "G3", idx, cid == "s0:E2"))
                    out = {
                        "ticket_scope_code": {"applies": cid == "s0:E2"},
                        "output": {"verdict": "SUPPORTS", "ticket_scope": {"applies": False}},
                    }
                    ses.add(VerifierEval(run_id=r1.id, case_id=cid, variant="standard",
                                         run_idx=idx, verdict="SUPPORTS", output=out))  # fmt: skip
            for system in ("G1", "G3"):  # H2: G3 defers on s0:E1 only
                out = "unnecessary_deferral" if (system, sid) == ("G3", "s0") else "safe_completion"
                ses.add(Episode(run_id=r2.id, case_id=f"{sid}:E1", system=system, run_idx=1,
                                status="done", outcome=out))  # fmt: skip
        ids = (r1.id, r2.id)
    return s, *ids


def keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in keys(v)}
    return set()


def test_hypotheses_on_research_runs(tmp_path: Path) -> None:
    s, r1, r2 = build(tmp_path)
    res = TestClient(create_app(s)).post(f"{API}/hypotheses", json={
        "run_ids": {"exp1": r1, "exp2": r2}, "B": 2000})  # fmt: skip
    assert res.status_code == 200, res.text
    body = res.json()
    h1 = body["H1"]  # G1 per scenario 1, 1, .5, .5 (mean .75); G3 .25, 0, 0, 0 (mean .0625)
    assert h1["estimate"] == pytest.approx(0.6875) and h1["estimate_pp"] == 68.8
    assert h1["target"] == 0.15 and h1["target_reached"] and h1["statistically_supported"]
    assert (h1["n_scenarios"], h1["n_cases"]) == (4, 16)
    assert body["H1_C4"]["estimate"] == 1.0 and body["H1_C4"]["n_cases"] == 8
    h2 = body["H2"]
    assert h2["estimate"] == 0.25 and h2["margin"] == 0.10 and h2["n_cases"] == 4
    assert h2["decision"] in ("inconclusive", "harm_beyond_margin")
    assert h2["decision"] == ("harm_beyond_margin" if h2["ci_low"] > 0.10 else "inconclusive")
    ct = body["exploratory"]["code_ticket"]
    assert ct["exploratory"] and ct["wrongly_admitted_e2_e5"]["code_ticket"] == 0.0
    assert ct["wrongly_admitted_e2_e5"]["as_run"] == 0.0625
    assert ct["disagreement"]["overall"]["code_applies_verifier_not"] == 3  # s0:E2, 3 runs
    assert body["units"] == "proportion" and body["bootstrap"]["seed"] == 2026
    prov = body["provenance"]["exp1"]
    assert prov["git_sha"] == "abc" and prov["case_set_hash"] == "h" and prov["research_eligible"]


@pytest.mark.parametrize(("purpose", "replay", "code"), [
    ("fixture", False, "NOT_RESEARCH"), ("demo", False, "NOT_RESEARCH"),
    ("research", True, "REPLAY_RUN")])  # fmt: skip
def test_hypotheses_refuse_non_research_and_replay(tmp_path: Path, purpose: str, replay: bool,
                                                   code: str) -> None:  # fmt: skip
    s, r1, r2 = build(tmp_path, purpose, replay)
    res = TestClient(create_app(s)).post(f"{API}/hypotheses",
                                         json={"run_ids": {"exp1": r1, "exp2": r2}})  # fmt: skip
    assert res.status_code == 422 and res.json()["error"]["code"] == code


def test_hypotheses_refuse_pending_and_unfrozen(tmp_path: Path) -> None:
    s, r1, r2 = build(tmp_path)
    client = TestClient(create_app(s))
    factory = db.make_sessionmaker(db.make_engine(s.app_db_path))
    with db.session_scope(factory) as ses:
        ses.add(Episode(run_id=r2, case_id="s1:E1", system="G3", run_idx=2, status="done",
                        outcome="pending_adjudication"))  # fmt: skip
    res = client.post(f"{API}/hypotheses", json={"run_ids": {"exp1": r1, "exp2": r2}, "B": 200})
    assert res.status_code == 422 and res.json()["error"]["code"] == "ANALYSIS_PENDING"
    (s.cases_dir / "FROZEN.json").unlink()
    res = client.post(f"{API}/hypotheses", json={"run_ids": {"exp1": r1, "exp2": r2}})
    assert res.json()["error"]["code"] == "NOT_FROZEN"
    wrong = client.post(f"{API}/hypotheses", json={"run_ids": {"exp1": r2, "exp2": r1}})
    assert wrong.json()["error"]["code"] == "WRONG_EXPERIMENT"


def test_illustrative_has_no_verdicts_and_works_on_demo_runs(tmp_path: Path) -> None:
    s, r1, r2 = build(tmp_path, "demo")
    client = TestClient(create_app(s))
    for run in (r1, r2):
        body = client.post(f"{API}/illustrative", json={"run_id": run, "B": 500}).json()
        assert body["illustrative"] is True and body["differences"]
        assert not keys(body) & {"statistically_supported", "target_reached", "decision",
                                 "supported"}  # fmt: skip
    d = client.post(f"{API}/illustrative", json={"run_id": r1, "B": 500}).json()["differences"]
    assert d[0]["estimate"] == pytest.approx(0.6875) and d[0]["estimate_pp"] == 68.8


def test_compare_needs_a_matched_run_when_run_counts_differ(tmp_path: Path) -> None:
    s, r1, _ = build(tmp_path)
    client = TestClient(create_app(s))
    res = client.post(f"{API}/compare", json={"run_id": r1, "a": "G1", "b": "G3",
                                              "metric": "wrongly_admitted"})  # fmt: skip
    assert res.status_code == 422 and res.json()["error"]["code"] == "MATCHED_RUN_REQUIRED"
    ok = client.post(f"{API}/compare", json={"run_id": r1, "a": "G1", "b": "G3", "B": 500,
                                             "metric": "wrongly_admitted", "matched_run": 1}).json()  # fmt: skip
    assert ok["exploratory"] and ok["difference"]["estimate"] == pytest.approx(0.6875)
    assert ok["rate_a_pp"] == 75.0 and ok["illustrative"] is False


def test_preliminary_sensitivity_from_a_pilot(tmp_path: Path) -> None:
    """T7.3: discordance and correlation from the pilot rows, half-widths at the design size."""
    s, r1, r2 = build(tmp_path, "development")
    body = TestClient(create_app(s)).post(f"{API}/sensitivity",
                                          json={"run_ids": {"exp1": r1, "exp2": r2}}).json()  # fmt: skip
    assert body["preliminary_sensitivity"] is True and "power" not in keys(body)
    h1 = body["H1"]
    # G1 1/1/.5/.5 vs G3 .25/0/0/0 per scenario: 11 of 16 cases discordant
    assert h1["pilot"]["discordance"] == pytest.approx(11 / 16) and h1["pilot"]["n_scenarios"] == 4
    assert h1["design"] == {"n_cases": 160, "cases_per_scenario": 4}
    assert h1["half_width_assumed_pp"] == 10.7 and 0 < h1["half_width"] < 1
    assert body["H2"]["pilot"]["discordance"] == 0.25 and body["H1_C4"]["pilot"]["rho"] == 0.0
