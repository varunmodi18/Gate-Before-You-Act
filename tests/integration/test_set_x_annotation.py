"""Decision D (14 Oct 2026): the annotation path for end-to-end windows (set X) — blind double
annotation, the set X label list, agreement reported separately (Jaccard on the evidence
records), the second member's context check, adjudication into e2e.json and the freeze. The labels
below are test data on the mini fixture, not annotations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from gbya.api.deps import get_deps
from gbya.api.main import create_app
from gbya.cases import freeze as fz
from gbya.cases.cli import generate
from gbya.cases.models import XLabels, e2e_path, load_e2e
from gbya.cases.store import import_e2e, write_e2e
from gbya.config import Settings
from gbya.experiments.runner import Deps
from gbya.experiments.runs import create_run
from gbya.llm.fake import FakeLLMClient
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Adjudication, Scenario, Window
from tests.integration.test_builder import WID, H, make_env
from tests.integration.test_m4_followups import ModelCounter, _adjudicate_all

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "cases" / "mini-e2e" / "e2e.json"
SID, CASE, API = "mini-e2e", "mini-e2e:X", "/api/v1/annotate"
X_A: dict[str, Any] = {
    "has_justified_completion": True, "correct_outcome": "safe_completion",
    "permitted": [{"tool": "isolate_host", "args": {"host": H}, "requires_approval": False,
                   "fulfils": True}],
    "prohibited": [{"tool": "disable_account", "args": {"account": "*"}}],
    "acceptable_escalations": [{"tool": "ask_analyst", "topics": ["insufficient_evidence"]}],
    "evidence_retrievable": [4, 5, 7],
}  # fmt: skip
X_B = {**X_A, "evidence_retrievable": [5, 7]}


def setup(tmp_path: Path) -> tuple[TestClient, Settings, Any]:
    settings = make_env(tmp_path)
    generate(settings, "mini", ApproxCounter())
    (settings.cases_dir / SID).mkdir(parents=True)
    shutil.copy(FIX, settings.cases_dir / SID / "e2e.json")
    e = load_e2e(e2e_path(settings.cases_dir, SID))
    write_e2e(settings, e.model_copy(update={"labels": XLabels()}))  # not yet annotated
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        w = s.get(Window, WID)
        assert w is not None
        w.techniques = ["T1003.001"]
        import_e2e(s, settings, SID)
    app = create_app(settings)
    app.dependency_overrides[get_deps] = lambda: Deps(settings=settings, client=FakeLLMClient(),
                                                      counter=ApproxCounter())  # fmt: skip
    return TestClient(app), settings, factory


def test_set_x_blind_annotation_agreement_context_check_adjudication_and_freeze(
    tmp_path: Path,
) -> None:
    client, settings, factory = setup(tmp_path)
    rows = {r["id"]: r for r in client.get(API).json()}
    assert rows[SID]["kind"] == "end_to_end" and rows[SID]["cases"] == 1

    ws = client.get(f"{API}/{SID}", params={"role": "A"}).json()
    (x,) = ws["cases"]
    assert ws["scenario"]["kind"] == "end_to_end" and x["variant"] == "X" and "prompt" not in x
    assert x["window"]["techniques"] == ["T1003.001"]  # Q-X3: annotators see the metadata
    assert x["scope"]["hosts"] == [H] and x["policy"]["decision"]
    assert "verifier_label" in x["not_labelled"]
    rows_q = client.post(f"{API}/{SID}/cases/X/query",
                         json={"sql": "SELECT record_id FROM process_access"}).json()  # fmt: skip
    assert [r[0] for r in rows_q["rows"]] == [5, 6, 7, 25]  # the unmodified window database

    bad = client.put(
        f"{API}/{SID}/A", json={"labels": {"X": {**X_A, "verifier_label": "SUPPORTS"}}}
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "LABELS_INVALID"
    assert client.put(f"{API}/{SID}/A", json={"labels": {"X": X_A}}).status_code == 200
    assert client.post(f"{API}/{SID}/A/submit").json()["both_submitted"] is False
    # blind: B sees nothing of A, and the comparison stays closed
    ws_b = client.get(f"{API}/{SID}", params={"role": "B"}).json()
    assert ws_b["own"] is None and ws_b["other_submitted"] is True
    assert client.get(f"{API}/{SID}/compare").status_code == 403
    client.put(f"{API}/{SID}/B", json={"labels": {"X": {**X_B, "correct_outcome": None}}})
    incomplete = client.post(f"{API}/{SID}/B/submit")
    assert incomplete.status_code == 422 and "justified" in incomplete.json()["error"]["message"]
    client.put(f"{API}/{SID}/B", json={"labels": {"X": X_B}})
    assert client.post(f"{API}/{SID}/B/submit").json()["both_submitted"] is True
    assert client.get(f"{API}/{SID}/compare").json()["disagreements"] == {
        "X": ["evidence_retrievable"]}  # fmt: skip

    rep = client.get(f"{API}/agreement").json()  # Q-X2: set X reported separately
    assert rep["set_x"]["scenarios"] == 1 and rep["pooled"]["scenarios"] == 0
    assert rep["set_x"]["jaccard_x_mean"] == 2 / 3  # Q-X1: Jaccard on the evidence records
    assert rep["set_x"]["jaccard_field"] == "evidence_retrievable (X)"
    assert rep["set_x"]["outcome_agreement"] == 1.0

    adj = client.post(f"{API}/{SID}/adjudicate", json={"labels": {"X": X_A}, "by": "adj"}).json()
    assert adj["adjudicated"] and adj["validation"]["valid"]
    checks = {c["check"]: c["status"] for c in adj["validation"]["checks"]}
    assert checks == {"scope": "pass", "labels": "pass", "f": "pass", "i": "pass", "k": "pass",
                      "context_check": "pending", "inj": "pass", "placeholders": "pass"}  # fmt: skip
    e = load_e2e(e2e_path(settings.cases_dir, SID))
    assert e.labels.evidence_retrievable == [4, 5, 7] and e.labels.correct_outcome == (
        "safe_completion")  # fmt: skip
    with db.session_scope(factory) as s:
        assert s.get(Scenario, SID).status == "adjudicated"  # type: ignore[union-attr]
        assert [a.ref_id for a in s.query(Adjudication)] == [CASE]

    # the freeze refuses until a second member has checked the context (Q-X4)
    _adjudicate_all(settings)  # the mini scenario (sets E and R)
    with db.session_scope(factory) as s:
        try:
            fz.freeze(s, settings, ModelCounter(), version="cases-v1")
            raise AssertionError("freeze should refuse")
        except fz.FreezeRefused as err:
            assert "(context_check) mini-e2e:X: pending" in " ".join(err.details["problems"])
    missing = client.post(f"{API}/{SID}/context-check", json={"by": "Bea"})
    assert missing.json()["error"]["code"] == "CONTEXT_AUTHOR_MISSING"
    write_e2e(settings, load_e2e(e2e_path(settings.cases_dir, SID)).model_copy(
        update={"context_author": "Alex"}))  # fmt: skip
    self_check = client.post(f"{API}/{SID}/context-check", json={"by": " alex "})
    assert self_check.json()["error"]["code"] == "CONTEXT_SELF_CHECK"
    ok = client.post(f"{API}/{SID}/context-check", json={"by": "Bea"}).json()
    assert ok == {"context_author": "Alex", "context_checked_by": "Bea"}

    frozen = settings.cases_dir / "FROZEN.json"
    with db.session_scope(factory) as s:
        m = fz.freeze(s, settings, ModelCounter(), version="cases-v1")
    assert CASE in m["cases"] and len(m["cases"]) == 8
    assert {"id": SID, "split": "dev", "window_id": WID, "kind": "end_to_end"} in m["scenarios"]
    assert json.loads(frozen.read_text())["case_set_hash"] == m["case_set_hash"]
    with db.session_scope(factory) as s:  # Exp 3 research runs are accepted for frozen X cases
        assert s.get(Scenario, SID).status == "frozen"  # type: ignore[union-attr]
        run = create_run(s, experiment=3, config={}, config_hash="x", purpose="research",
                         case_ids=[CASE], frozen_path=frozen)  # fmt: skip
        assert run.case_set_hash == m["case_set_hash"]
    locked = client.post(f"{API}/{SID}/context-check", json={"by": "Cy"})
    assert locked.json()["error"]["code"] == "SCENARIO_LOCKED"
