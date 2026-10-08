"""T3.5: Exp 1 runner — Exp 1V + Exp 1G on the hand-made fixture with the Fake LLM, idempotency,
the composition-equivalence test (§D.7.2), C4 invocation counts, the runs API and a real
kill-and-resume of the worker process."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from gbya.api.main import create_app
from gbya.cases.handmade import insert_fixture
from gbya.config import REPO_ROOT, Settings
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import normalise_window
from gbya.experiments import runner as rn
from gbya.experiments.exp1 import (
    Package,
    call_from_row,
    comparable,
    compose,
    decide,
    decision_row,
    eval_row,
    judge,
    load_case,
)
from gbya.gate import verifier as vf
from gbya.gate.config import load_configs
from gbya.gate.gate import Gate
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.tokens import ApproxCounter
from gbya.policy.engine import PolicyEngine, PolicyFile
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.store import db
from gbya.store.models import GateDecisionRow, Job, JobItem, Run, VerifierEval

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
SPEC = {"verifier_variants": ["standard", "rationale", "none"], "verifier_runs": 3,
        "code_only": ["G0", "G1", "G2", "A1"], "composed": ["G3", "A2", "A3", "A4"]}  # fmt: skip
CASES = ["hm:E1", "hm:E3", "hm:R_neg"]
N_EVALS = 3 * 3 * 3  # cases x variants x runs
N_GATES = 3 * 4 + 3 * 4 * 3  # code-only x1 + composed x3


def out(verdict: str, reason: str = "scripted") -> dict[str, Any]:
    return {"verdict": verdict, "unmet_requirement": None if verdict == "SUPPORTS" else "x",
            "ticket_scope": {"applies": False, "matches": dict.fromkeys(
                ("host", "account", "command", "time"), False)}, "reason": reason}  # fmt: skip


def scripted() -> FakeLLMClient:
    """hm:E3 cites DC records 19 and 22 → INSUFFICIENT; everything else SUPPORTS."""
    return FakeLLMClient([FakeRule([out("INSUFFICIENT")], pattern='"record_id": 22'),
                          FakeRule([out("SUPPORTS")])])  # fmt: skip


def setup_env(tmp: Path, **settings: Any) -> Settings:
    duck = tmp / "mini.duckdb"
    meta = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    normalise_window(parse_metadata(meta, MINI), MINI, duck)
    app_db = tmp / "app.db"
    db.upgrade(app_db)
    with db.session_scope(db.make_sessionmaker(db.make_engine(app_db))) as s:
        insert_fixture(s, duck)
    ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                             "0" * 64, FIX / "attack/LICENSE.txt"), tmp / "index")  # fmt: skip
    return Settings(app_db_path=app_db, data_dir=tmp, frontend_dist=tmp / "none", env="test",
                    llm_backend="fake", worker_concurrency=2, **settings)  # fmt: skip


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> tuple[Settings, Any, int]:
    settings = setup_env(tmp_path_factory.mktemp("exp1"))
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        run = rn.create_exp1_run(s, settings, purpose="fixture", case_ids=CASES, overrides=SPEC)
        run_id = run.id
    deps = rn.Deps(settings=settings, client=scripted(), counter=ApproxCounter())
    status = rn.execute_run(factory, run_id, deps)
    assert status == "completed"
    return settings, factory, run_id


def _count(factory: Any, model: Any, run_id: int) -> int:
    with db.session_scope(factory) as s:
        return int(
            s.scalar(select(func.count()).select_from(model).where(model.run_id == run_id)) or 0
        )


def test_plan_items_puts_exp1v_first() -> None:
    items = rn.plan_items(rn.Exp1Spec.model_validate(SPEC), ["b", "a"])
    assert len(items) == 2 * (9 + 4 + 12)
    assert items[0] == ("a", "V:standard", 1) and items[17][1].startswith("V:")
    assert ("a", "G1", 1) in items and ("a", "G1", 2) not in items  # code-only: one run (A-13)
    assert ("b", "A4", 3) in items


def test_spec_validation() -> None:
    with pytest.raises(ValueError, match="needs verifier variant rerank"):
        rn.load_spec(overrides={**SPEC, "composed": ["A6"]})
    with pytest.raises(ValueError, match="has C4"):
        rn.load_spec(overrides={**SPEC, "code_only": ["G3"]})
    full = rn.load_spec()
    assert full.verifier_variants == ["standard", "rationale", "none", "rerank"]
    assert full.composed == ["G3", "A2", "A3", "A4", "A6"] and full.verifier_runs == 3


def test_demo_run_completes_with_every_unit_once(env: tuple[Settings, Any, int]) -> None:
    _, factory, run_id = env
    assert _count(factory, VerifierEval, run_id) == N_EVALS
    assert _count(factory, GateDecisionRow, run_id) == N_GATES
    with db.session_scope(factory) as s:
        statuses = dict(s.execute(select(JobItem.status, func.count()).where(
            JobItem.run_id == run_id).group_by(JobItem.status)).all())  # fmt: skip
        run = s.get(Run, run_id)
        assert run is not None and run.purpose == "fixture" and run.backend == "fake"
        assert run.git_sha and run.config["spec"]["composed"] == SPEC["composed"]
        assert len(run.git_sha.removesuffix("+dirty")) == 40
        e3 = s.scalars(select(VerifierEval).where(VerifierEval.run_id == run_id,
                                                  VerifierEval.case_id == "hm:E3")).all()  # fmt: skip
        assert {e.verdict for e in e3} == {"INSUFFICIENT"} and all(e.manifest for e in e3)
        assert all(e.prompt_hash and e.output and e.output["retrieval"] is not None
                   for e in e3 if e.variant != "none")  # fmt: skip
    assert statuses == {"done": N_EVALS + N_GATES}


def test_rerunning_a_finished_run_writes_nothing(env: tuple[Settings, Any, int]) -> None:
    settings, factory, run_id = env
    deps = rn.Deps(settings=settings, client=scripted(), counter=ApproxCounter())
    rn.execute_run(factory, run_id, deps)
    assert deps.client.calls == []  # type: ignore[attr-defined]
    assert _count(factory, VerifierEval, run_id) == N_EVALS
    assert _count(factory, GateDecisionRow, run_id) == N_GATES


def test_c4_invocation_counts_where_c3_rejects(env: tuple[Settings, Any, int]) -> None:
    settings, _, run_id = env
    client = TestClient(create_app(settings))
    body = client.get(f"/api/v1/runs/{run_id}/verifier-eval").json()
    g3, a2 = (
        body["gate_path"]["G3"]["by_case_variant"]["E3"],
        body["gate_path"]["A2"]["by_case_variant"]["E3"],
    )
    assert (g3["packages"], g3["reached_c4"]) == (3, 0)  # C3 stops the wrong-target package
    assert (a2["packages"], a2["reached_c4"]) == (3, 3)  # no C3: the verifier sees it
    assert a2["conditional"]["exact_accuracy"] == 1.0  # INSUFFICIENT = label
    std = body["diagnostic"]["standard"]
    assert std["n"] == 9 and std["exact_accuracy"] == 1.0 and std["parse_errors"] == 0
    assert {k: v["n"] for k, v in std["by_case_variant"].items()} == {"E1": 3, "E3": 3, "R_neg": 3}
    assert std["confusion"]["INSUFFICIENT"]["INSUFFICIENT"] == 3
    assert set(body["diagnostic"]) == {"standard", "rationale", "none"}
    assert body["purpose"] == "fixture" and body["research_eligible"] is False


# ---- equivalence of composition and an inline gate call (§D.7.2) -----------------------------------


FORBID = PolicyEngine(
    PolicyFile.model_validate({"version": 1, "default": "forbidden", "rules": []})
)


@pytest.mark.parametrize(
    ("label", "patch", "verdict", "policy", "stops_at"),
    [
        ("C1", {"args": {"host": "NOPE.lab.local"}}, "SUPPORTS", None, "C1"),
        ("C2", {"cited": (5, 999)}, "SUPPORTS", None, "C2"),
        ("C3", {"cited": (19, 22)}, "SUPPORTS", None, "C3"),
        ("C4", {}, "CONTRADICTED", None, "C4"),
        ("C5", {}, "SUPPORTS", FORBID, "C5"),
        ("C6", {"args": {"host": "DC-01.lab.local"}, "cited": (19,)}, "SUPPORTS", None, "C6"),
        ("admit", {}, "SUPPORTS", None, None),
    ],
)
def test_composition_equals_inline_gate_call(
    env: tuple[Settings, Any, int], label: str, patch: dict[str, Any], verdict: str,
    policy: PolicyEngine | None, stops_at: str | None,
) -> None:  # fmt: skip
    settings, factory, _ = env
    with db.session_scope(factory) as s:
        base = load_case(s, "hm:E1", settings)
    case = replace(base, package=replace(base.package, **patch))
    assert isinstance(case.package, Package)
    pol = policy or PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")
    g3 = load_configs()["G3"]
    index = ix.load(settings.data_dir / "index")
    counter = ApproxCounter()

    def verifier() -> vf.LLMVerifier:  # same scripted output, a fresh client per path
        client = FakeLLMClient([FakeRule([out(verdict)])])
        return vf.LLMVerifier(client, "standard", rn.Deps(settings=settings, client=client,
                              counter=counter).requirements, vf.live_retriever(index))  # fmt: skip

    inline, ms1 = decide(case, Gate(g3, pol, verifier()), counter)
    stored = call_from_row(eval_row(1, case.case_id, 1, judge(case, verifier(), counter)))
    composed, ms2 = compose(case, g3, pol, stored, counter)
    a = comparable(decision_row(inline, case, 1, 1, ms1))
    b = comparable(decision_row(composed, case, 1, 1, ms2))
    assert a == b, label
    assert inline.failed_check == stops_at
    if stops_at in (None, "C4", "C5", "C6"):
        assert b["verifier"] is not None and b["verifier"]["prompt_hash"] == stored.prompt_hash


# ---- runs API -----------------------------------------------------------------------------------------


def test_runs_api_create_cancel_resume_and_progress(tmp_path: Path) -> None:
    settings = setup_env(tmp_path)
    client = TestClient(create_app(settings))
    r = client.post("/api/v1/runs", json={"purpose": "fixture", "case_ids": CASES, "config": SPEC})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued" and run["progress"]["total"] == N_EVALS + N_GATES
    assert run["backend"] == "fake" and run["research_eligible"] is False
    rid = run["id"]
    assert client.post(f"/api/v1/runs/{rid}/cancel").json()["status"] == "cancelled"
    resumed = client.post(f"/api/v1/runs/{rid}/resume").json()
    assert resumed["status"] == "queued"
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        assert s.scalar(select(func.count()).select_from(Job).where(
            Job.run_id == rid, Job.status == "queued")) == 1  # fmt: skip
    deps = rn.Deps(settings=settings, client=scripted(), counter=ApproxCounter())
    assert rn.execute_run(factory, rid, deps) == "completed"
    with client.stream("GET", f"/api/v1/runs/{rid}/progress") as resp:
        text = "".join(resp.iter_text())
    assert "event: progress" in text and '"status": "completed"' in text
    assert f'"done": {N_EVALS + N_GATES}' in text
    research = client.post("/api/v1/runs", json={"purpose": "research", "case_ids": CASES})
    assert (
        research.status_code == 422 and research.json()["error"]["code"] == "RESEARCH_RUN_REFUSED"
    )
    bad = client.post(
        "/api/v1/runs",
        json={"case_ids": CASES, "config": {"verifier_variants": ["standard"], "composed": ["A6"]}},
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_RUN_CONFIG"
    assert client.post("/api/v1/runs", json={"case_ids": ["nope"]}).status_code == 404
    assert client.get("/api/v1/runs/999").json()["error"]["code"] == "RUN_NOT_FOUND"


def test_failed_units_are_recorded_and_the_run_is_failed(tmp_path: Path) -> None:
    settings = setup_env(tmp_path)
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    spec = {**SPEC, "verifier_variants": [*SPEC["verifier_variants"], "rerank"],
            "composed": [*SPEC["composed"], "A6"]}  # fmt: skip
    with db.session_scope(factory) as s:
        rid = rn.create_exp1_run(s, settings, purpose="fixture", case_ids=CASES, overrides=spec).id
    deps = rn.Deps(settings=settings, client=scripted(), counter=ApproxCounter())  # no reranker
    assert rn.execute_run(factory, rid, deps) == "failed"
    with db.session_scope(factory) as s:
        errs = s.scalars(
            select(JobItem).where(JobItem.run_id == rid, JobItem.status == "error")
        ).all()
        assert {e.system for e in errs} == {"V:rerank", "A6"} and len(errs) == 3 * 3 * 2
        assert any("RerankerUnavailable" in (e.error or "") for e in errs)
        assert any("no Exp 1V output" in (e.error or "") for e in errs)
        assert s.scalar(select(func.count()).select_from(VerifierEval).where(
            VerifierEval.run_id == rid, VerifierEval.variant == "rerank")) == 0  # fmt: skip


# ---- kill and resume (a real worker process, SIGKILL) ---------------------------------------------------


def _worker(settings: Settings, log: Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, "GBYA_ENV": "test", "GBYA_APP_DB_PATH": str(settings.app_db_path),
           "GBYA_DATA_DIR": str(settings.data_dir), "GBYA_LOG_DIR": str(settings.data_dir / "logs"),
           "GBYA_LLM_BACKEND": "fake", "GBYA_FAKE_LLM_DELAY_MS": "150",
           "GBYA_WORKER_HEARTBEAT_S": "1", "GBYA_WORKER_STALE_S": "3",
           "GBYA_WORKER_CONCURRENCY": "2"}  # fmt: skip
    env.pop("VIRTUAL_ENV", None)
    return subprocess.Popen([sys.executable, "-m", "gbya.worker"], env=env,
                            stdout=log.open("wb"), stderr=subprocess.STDOUT)  # fmt: skip


def _done(factory: Any, rid: int) -> int:
    with db.session_scope(factory) as s:
        return int(s.scalar(select(func.count()).select_from(JobItem).where(
            JobItem.run_id == rid, JobItem.status == "done")) or 0)  # fmt: skip


def test_worker_kill_and_resume_has_no_duplicates(tmp_path: Path) -> None:
    settings = setup_env(tmp_path)
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        rid = rn.create_exp1_run(s, settings, purpose="fixture", case_ids=CASES, overrides=SPEC).id
    first = _worker(settings, tmp_path / "w1.log")
    deadline = time.monotonic() + 60
    while _done(factory, rid) < 8 and time.monotonic() < deadline:
        time.sleep(0.1)
    os.kill(first.pid, signal.SIGKILL)
    first.wait()
    at_kill = _done(factory, rid)
    assert 8 <= at_kill < N_EVALS + N_GATES, (tmp_path / "w1.log").read_text()[-2000:]
    with db.session_scope(factory) as s:
        job = s.scalar(select(Job).where(Job.run_id == rid))
        assert job is not None and job.status == "running"  # orphaned: re-claimable when stale

    second = _worker(settings, tmp_path / "w2.log")
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            with db.session_scope(factory) as s:
                run = s.get(Run, rid)
                if run is not None and run.status in ("completed", "failed"):
                    break
            time.sleep(0.2)
    finally:
        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)
    with db.session_scope(factory) as s:
        run = s.get(Run, rid)
        assert run is not None and run.status == "completed", (tmp_path / "w2.log").read_text()[
            -3000:
        ]
        dup_evals = s.execute(select(VerifierEval.case_id, VerifierEval.variant, VerifierEval.run_idx,
                                     func.count()).where(VerifierEval.run_id == rid)
                              .group_by(VerifierEval.case_id, VerifierEval.variant,
                                        VerifierEval.run_idx).having(func.count() > 1)).all()  # fmt: skip
        dup_gates = s.execute(select(GateDecisionRow.case_id, GateDecisionRow.system,
                                     GateDecisionRow.run_idx, func.count())
                              .where(GateDecisionRow.run_id == rid)
                              .group_by(GateDecisionRow.case_id, GateDecisionRow.system,
                                        GateDecisionRow.run_idx).having(func.count() > 1)).all()  # fmt: skip
        attempts = s.scalars(select(JobItem.attempts).where(JobItem.run_id == rid)).all()
    assert dup_evals == [] and dup_gates == []
    assert _count(factory, VerifierEval, rid) == N_EVALS
    assert _count(factory, GateDecisionRow, rid) == N_GATES
    assert all(a >= 1 for a in attempts)
    print(
        f"\nkilled after {at_kill} done units; units retried after the kill: "
        f"{sum(1 for a in attempts if a > 1)}"
    )
