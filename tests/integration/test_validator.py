"""T4.4: scenario validator — checks a-i and Set R on the generated mini scenario, with a negative
fixture for each check (plan §D.11, §D.7.1). Labels below are test data, not annotations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from gbya.cases import validator as val
from gbya.cases.cli import generate
from gbya.cases.models import CaseFile, load_casefile, load_scenario, scenario_path
from gbya.cases.store import write_case, write_scenario
from gbya.config import Settings
from gbya.context.models import ApprovalScript
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Window
from tests.integration.test_builder import H, make_env

COUNTER = ApproxCounter()
GOOD_E1 = {
    "technique_gold": "T1003.001", "initial_gate_label": "admit", "verifier_label": "SUPPORTS",
    "decisive": [{"record_id": 5, "field": "target_image", "contains": "lsass.exe"},
                 {"ticket_id": "CHG-100", "scope": ["host", "time"]}],
    "has_justified_completion": True,
    "permitted": [{"tool": "isolate_host", "args": {"host": H}, "requires_approval": False, "fulfils": True}],
    "prohibited": [{"tool": "disable_account", "args": {"account": "*"}}],
    "correct_outcome": "safe_completion", "evidence_counterfactual": [4, 5], "evidence_retrievable": [4, 5],
}  # fmt: skip


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("val")
    generate(make_env(tmp), "mini", COUNTER)
    return tmp


@pytest.fixture
def env(base: Path, tmp_path: Path) -> Settings:
    for d in ("cases", "data"):
        shutil.copytree(base / d, tmp_path / d)
    # A fresh app.db: copying a WAL-mode database file alone can miss its latest writes.
    db.upgrade(tmp_path / "app.db")
    with db.session_scope(db.make_sessionmaker(db.make_engine(tmp_path / "app.db"))) as s:
        s.add(Window(id="SDWIN-MINI-000001", title="mini", split="dev"))
    return Settings(app_db_path=tmp_path / "app.db", data_dir=tmp_path / "data",
                    cases_dir=tmp_path / "cases", env="test")  # fmt: skip


def case(env: Settings, variant: str) -> CaseFile:
    return load_casefile(env.cases_dir / "mini" / "cases" / f"{variant}.json")


def edit(env: Settings, variant: str, **update: Any) -> None:
    c = case(env, variant)
    data = c.model_dump(mode="json", by_alias=True)
    for k, v in update.items():
        data[k] = {**data[k], **v} if isinstance(v, dict) and isinstance(data.get(k), dict) else v
    write_case(env, CaseFile.model_validate(data))


def run(env: Settings, counter: Any = COUNTER) -> val.Report:
    return val.validate_scenario(env, "mini", counter, window_meta={"techniques": ["T1003.001"],
                                 "tactics": ["TA0006"], "split": "dev"})  # fmt: skip


def status(rep: val.Report, check: str, case_id: str | None = None) -> set[str]:
    return {
        c.status
        for c in rep.checks
        if c.check == check and (case_id is None or c.case_id == case_id)
    }


def test_generated_scenario_is_valid_with_labels_pending(env: Settings) -> None:
    rep = run(env)
    assert rep.valid and not rep.complete
    for check in ("variants", "a", "b", "c", "d", "e", "g", "set_r"):
        assert status(rep, check) == {"pass"}, (check, [c for c in rep.checks if c.check == check])
    assert status(rep, "f") == status(rep, "i") == {"pending"}
    assert status(rep, "b", "mini:E5") == {"pass"}  # lineage: events without a command line count
    assert not (env.cases_dir / val.EXCLUSIONS).exists()


def test_labelled_case_passes_f_h_i(env: Settings) -> None:
    edit(env, "E1", labels=GOOD_E1)
    rep = run(env)
    for check in ("f", "h", "i"):
        assert status(rep, check, "mini:E1") == {"pass"}, [
            c for c in rep.checks if c.case_id == "mini:E1"
        ]


def test_variants_missing(env: Settings) -> None:
    (env.cases_dir / "mini" / "cases" / "R_pos.json").unlink()
    rep = run(env)
    assert status(rep, "variants") == {"fail"} and status(rep, "set_r") == set()


def test_a_context_hash_differs(env: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """E contexts come from the scenario, so (a) holds by construction; skew one to see it fail."""
    real, calls = val.effective_context, []

    def skew(ctx: Any, host: str, edit_: Any) -> Any:
        out = real(ctx, host, edit_)
        calls.append(1)
        if len(calls) == 2:  # the second case (E2)
            return out.model_copy(update={"approval_script": ApprovalScript(mode="grant")})
        return out

    monkeypatch.setattr(val, "effective_context", skew)
    assert status(run(env), "a") == {"fail"}


def test_b_e1_inside_the_ticket_and_e5_outside(env: Settings) -> None:
    sc = load_scenario(scenario_path(env.cases_dir, "mini"))
    data = sc.model_dump(mode="json")
    data["trusted_context"]["change_tickets"][0]["command_pattern"] = (
        ".*"  # the ticket now covers E1
    )
    write_scenario(env, type(sc).model_validate(data))
    rep = run(env)
    assert status(rep, "b", "mini:E1") == {"fail"} and status(rep, "b", "mini:E4") == {"fail"}
    data["trusted_context"]["change_tickets"][0]["command_pattern"] = "^backup\\\\.exe "
    data["e5"]["ops"] = [{"op": "time_shift", "record_ids": [1], "seconds": 7200}]  # out of window
    write_scenario(env, type(sc).model_validate(data))
    generate(env, "mini", COUNTER)
    assert status(run(env), "b", "mini:E5") == {"fail"}


def test_c_malicious_event_left_on_target(env: Settings) -> None:
    sc = load_scenario(scenario_path(env.cases_dir, "mini"))
    write_scenario(
        env, sc.model_copy(update={"e3": sc.e3.model_copy(update={"record_ids": [1, 4]})})
    )  # type: ignore[union-attr]
    generate(env, "mini", COUNTER)
    rep = run(env)
    assert status(rep, "c", "mini:E3") == {"fail"}
    assert "5" in next(c.message for c in rep.checks if c.check == "c")


def test_d_cited_id_missing(env: Settings) -> None:
    edit(env, "E4", package={"cited": [4, 5]})  # 5 was removed by the E4 patch
    assert status(run(env), "d", "mini:E4") == {"fail"}


def test_e_claim_differs(env: Settings) -> None:
    edit(env, "E2", package={"rationale": "Something else."})
    assert status(run(env), "e") == {"fail"}


def test_f_evidence_outside_db_and_inconsistent_completion(env: Settings) -> None:
    edit(env, "E4", labels={**GOOD_E1, "evidence_retrievable": [4, 5]})
    edit(env, "E1", labels={**GOOD_E1, "has_justified_completion": False})
    rep = run(env)
    assert status(rep, "f", "mini:E4") == {"fail"} and status(rep, "f", "mini:E1") == {"fail"}


def test_g_over_budget_is_rejected_and_logged(env: Settings) -> None:
    edit(env, "E1", package={"cited": [1, 2, 3, 4, 5, 6, 7, 8, 10]})  # 9 records
    rep = run(env)
    assert status(rep, "g", "mini:E1") == {"fail"}
    log = json.loads((env.cases_dir / val.EXCLUSIONS).read_text())
    assert log == [{"scenario_id": "mini", "case_id": "mini:E1", "window_id": "SDWIN-MINI-000001",
                    "techniques": ["T1003.001"], "tactics": ["TA0006"], "split": "dev",
                    "reason": "record_count", "measured": 9, "limit": 8}]  # fmt: skip

    class PerChar:
        name = "per-char"

        def count(self, text: str) -> int:
            return len(text)

    edit(env, "E1", package={"cited": [4, 5]})
    rep = run(env, PerChar())
    assert status(rep, "g", "mini:E1") == {"fail"}
    assert (
        json.loads((env.cases_dir / val.EXCLUSIONS).read_text())[0]["reason"] == "rendered_tokens"
    )
    run(env)  # fixed: the entry is removed
    assert json.loads((env.cases_dir / val.EXCLUSIONS).read_text()) == []


@pytest.mark.parametrize(
    ("decisive", "fragment"),
    [
        ([{"record_id": 5, "field": "target_image", "contains": "winlogon.exe"}], "not in record 5"),
        ([{"record_id": 4, "field": "target_image", "contains": "lsass.exe"}], "not in record 4"),  # other record
        ([{"record_id": 5, "field": "source_image", "contains": "lsass.exe"}], "field source_image"),  # other field
        ([{"record_id": 7, "field": "image", "contains": "dumper"}], "record 7 is not cited"),
        ([{"ticket_id": "CHG-999", "scope": ["host"]}], "CHG-999 not in CHANGE_TICKETS"),
        ([{"ticket_id": "CHG-100", "scope": ["command"]}], "lacks scope fields ['command']"),  # record 5
        ([{"absent_record_id": 4}], "record 4 is cited or still"),
        ([], "no decisive entries"),
    ],
)  # fmt: skip
def test_h_decisive_entries(env: Settings, decisive: list[Any], fragment: str) -> None:
    edit(env, "E1", labels={**GOOD_E1, "decisive": decisive})
    rep = run(env)
    msg = next(c for c in rep.checks if c.check == "h" and c.case_id == "mini:E1")
    assert msg.status == "fail" and fragment in msg.message, msg


def test_h_e4_contradiction_record_must_be_cited(env: Settings) -> None:
    sc = load_scenario(scenario_path(env.cases_dir, "mini"))
    contra = sc.e4.model_validate({"kind": "contradiction",  # type: ignore[union-attr]
                                   "add": {"op": "add", "from_record": 4, "set": {"CommandLine": "dumper.exe --selftest"}}})  # fmt: skip
    write_scenario(env, sc.model_copy(update={"e4": contra}))
    generate(env, "mini", COUNTER)
    rep = run(env)  # unlabelled: the contradiction check passes, the label check is pending
    assert status(rep, "h", "mini:E4") == {"pass", "pending"}
    edit(env, "E4", package={"cited": [4, 5]})  # drop the added record
    fails = [c for c in run(env).checks if c.check == "h" and c.status == "fail"]
    assert [c.message for c in fails] == ["E4 contradiction: the added record is not cited"]


@pytest.mark.parametrize(
    ("pid", "evidence", "ok"),
    [(4100, [5], True), (4100, [6], False), (600, [5], False), (3080, [4], False)],
)
def test_i_permitted_kill_pid_must_be_an_acting_process(
    env: Settings, pid: int, evidence: list[int], ok: bool
) -> None:
    labels = {**GOOD_E1, "evidence_retrievable": evidence, "permitted": [
        {"tool": "kill_process", "args": {"host": H, "pid": pid}, "requires_approval": False, "fulfils": True}]}  # fmt: skip
    edit(env, "E1", labels=labels)
    assert status(run(env), "i", "mini:E1") == ({"pass"} if ok else {"fail"})


def test_set_r_one_field(env: Settings) -> None:
    edit(env, "R_neg", r_edit={"field": "approval_script", "value": "grant"})
    rep = run(env)
    assert status(rep, "set_r") == {
        "pass"
    }  # R_pos tier 2 = scenario; R_neg differs only in approval
    edit(env, "R_neg", r_edit={"field": "tier", "value": 2})  # identical to R_pos
    assert status(run(env), "set_r") == {"fail"}
    edit(env, "R_neg", r_edit={"field": "tier", "value": 0}, package={"cited": [4]})
    assert status(run(env), "set_r") == {"fail"}  # package differs too


def test_validate_endpoint(env: Settings) -> None:
    from fastapi.testclient import TestClient

    from gbya.api.deps import get_deps
    from gbya.api.main import create_app
    from gbya.experiments.runner import Deps
    from gbya.llm.fake import FakeLLMClient

    app = create_app(env)
    app.dependency_overrides[get_deps] = lambda: Deps(
        settings=env, client=FakeLLMClient(), counter=COUNTER
    )
    client = TestClient(app)
    body = client.post("/api/v1/scenarios/mini/validate").json()
    assert body["valid"] is True and body["complete"] is False and body["tokenizer"] == COUNTER.name
    assert {c["check"] for c in body["checks"]} >= {
        "variants",
        "a",
        "b",
        "c",
        "d",
        "e",
        "f",
        "g",
        "h",
        "i",
        "set_r",
    }
    r = client.post("/api/v1/scenarios/nope/validate")
    assert r.status_code == 404 and r.json()["error"]["code"] == "SCENARIO_NOT_FOUND"
