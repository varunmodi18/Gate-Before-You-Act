"""Decision A (13 Oct 2026): the scope of a case and rule 1 — computed scope, the validator's
*scope* and *k* checks, the Studio starter and its "Fill from the window" endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from gbya.cases.models import ScenarioFile, load_scenario, scenario_path
from gbya.cases.scope import compute_scope, differences, in_scope
from gbya.cases.store import window_db, write_scenario
from gbya.config import Settings
from gbya.data.connection import open_case_db
from tests.integration.test_builder import WID, H, make_env
from tests.integration.test_validator import GOOD_E1, edit, run, status
from tests.integration.test_validator import base as base
from tests.integration.test_validator import env as env

DC = "DC-01.lab.local"


@pytest.fixture(scope="module")
def window(tmp_path_factory: pytest.TempPathFactory) -> Path:
    s = make_env(tmp_path_factory.mktemp("scope"))
    return s.resolve(window_db(s, WID))


def test_scope_lists_what_acts_on_the_request_hosts(window: Path) -> None:
    con = open_case_db(window)
    try:
        sc = compute_scope(con, [H])
        both = compute_scope(con, [H, DC])
    finally:
        con.close()
    assert sc.accounts == ["a.mehta", "system"]
    # inbound source only: the destinations of WKSTN-01's outbound connections do not act on it
    assert sc.ips == ["203.0.113.9"]
    assert {(p.pid, p.image) for p in sc.processes} >= {(4100, "dumper.exe"), (716, "dumper.exe")}
    assert all(p.host == H for p in sc.processes)
    # logons and share accesses on the DC from 10.0.0.5 count once the DC is a scope host
    assert "10.0.0.5" in both.ips and {"svc_reports", "admin"} <= set(both.accounts)
    assert in_scope(sc, "isolate_host", {"host": H.upper()})
    assert not in_scope(sc, "isolate_host", {"host": DC})
    assert in_scope(sc, "kill_process", {"host": H, "pid": 4100})
    assert not in_scope(sc, "kill_process", {"host": DC, "pid": 4100})
    assert in_scope(sc, "disable_account", {"account": "LAB\\A.Mehta"})
    assert not in_scope(sc, "disable_account", {"account": "svc_reports"})
    assert in_scope(sc, "block_ip", {"ip": "203.0.113.9"})
    assert not in_scope(sc, "block_ip", {"ip": "203.0.113.7"})
    trimmed = sc.model_copy(update={"accounts": ["a.mehta", "nobody"]})
    assert differences(trimmed, sc) == [
        "accounts acting on the scope hosts but not listed: ['system']",
        "accounts listed but not acting on the scope hosts: ['nobody']",
    ]


def test_scope_is_required_and_names_the_target_host() -> None:
    sc = load_scenario(Path(__file__).parents[1] / "fixtures/cases/mini/scenario.json")
    data = sc.model_dump(mode="json", by_alias=True)
    with pytest.raises(ValidationError, match="scope"):
        ScenarioFile.model_validate({k: v for k, v in data.items() if k != "scope"})
    with pytest.raises(ValidationError, match="must include the target host"):
        ScenarioFile.model_validate({**data, "scope": {**data["scope"], "hosts": [DC]}})


def test_validator_checks_the_authored_scope(env: Settings) -> None:
    assert status(run(env), "scope") == {"pass"}
    path = scenario_path(env.cases_dir, "mini")
    sc = load_scenario(path)
    write_scenario(env, sc.model_copy(update={"scope": sc.scope.model_copy(
        update={"accounts": ["a.mehta"], "ips": ["203.0.113.7"]})}))  # fmt: skip
    rep = run(env)
    msg = next(c.message for c in rep.checks if c.check == "scope")
    assert status(rep, "scope") == {"fail"} and "['system']" in msg and "203.0.113.7" in msg


def test_rule_1_permitted_actions_stay_inside_each_case_scope(env: Settings) -> None:
    edit(env, "E1", labels=GOOD_E1)
    assert status(run(env), "k", "mini:E1") == {"pass"}
    # E3 moves the activity to HR001: isolating HR001 (where the evidence now points) is outside
    # the scope, and so is killing the dumper, which no longer acts on the request's host there
    e3 = {**GOOD_E1, "permitted": [
        {"tool": "isolate_host", "args": {"host": "HR001.lab.local"}, "requires_approval": False,
         "fulfils": False},
        {"tool": "kill_process", "args": {"host": H, "pid": 4100}, "requires_approval": False,
         "fulfils": False}]}  # fmt: skip
    edit(env, "E3", labels=e3)
    rep = run(env)
    msg = next(c.message for c in rep.checks if c.check == "k" and c.case_id == "mini:E3")
    assert status(rep, "k", "mini:E3") == {"fail"}
    assert "HR001.lab.local" in msg and "4100" in msg
