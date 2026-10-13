"""Team decisions after M4: E2 excludes attack-related processes and annotators can flag sampled
records; check (b) ignores reused PIDs; C3's E2 rejection rate per tool; ``make freeze-cases``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gbya.api.deps import get_deps
from gbya.api.main import create_app
from gbya.cases import freeze as fz
from gbya.cases.builder import e2_order, sample_e2, window_facts
from gbya.cases.cli import generate
from gbya.cases.models import DbPatch, load_casefile
from gbya.cases.patch import _insert, patch_database
from gbya.cases.processes import attack_related_records
from gbya.cases.store import import_scenario, write_case
from gbya.cases.validator import _command_of
from gbya.config import Settings
from gbya.data import fieldmap as fm
from gbya.data.build_db import build_database
from gbya.data.connection import open_case_db
from gbya.errors import Conflict
from gbya.experiments.runner import Deps
from gbya.experiments.runs import ResearchRunRefused, create_run
from gbya.gate.evidence import read_cited
from gbya.llm.fake import FakeLLMClient
from gbya.llm.tokens import ApproxCounter
from gbya.scoring.verifier_eval import C3Row, e2_c3_report
from gbya.store import db
from gbya.store.models import Scenario
from tests.integration.test_annotate_api import labels_a
from tests.integration.test_builder import WID, H, make_env, scenario


class ModelCounter:
    name = "model:test-tokenizer"

    def count(self, text: str) -> int:
        return len(text) // 4


# ---- attack-related processes (GUIDs, parents, children, PID reuse) --------------------------------


def _ev(eid: str, ts: str, **kw: Any) -> dict[str, Any]:
    return {"Hostname": H, "Channel": "Microsoft-Windows-Sysmon/Operational", "EventID": eid,
            "TimeCreated": f"2020-10-18T10:00:{ts}.000Z", **kw}  # fmt: skip


def test_attack_related_records_use_guids_parents_and_children(tmp_path: Path) -> None:
    events = {
        1: _ev("1", "01", Image="C:\\\\evil.exe", ProcessId="100", ProcessGuid="{A}", ParentProcessId="50", ParentProcessGuid="{P}"),
        2: _ev("10", "02", SourceImage="C:\\\\evil.exe", SourceProcessId="100", SourceProcessGUID="{A}", TargetProcessId="600"),
        3: _ev("1", "03", Image="C:\\\\ok.exe", ProcessId="100", ProcessGuid="{B}", ParentProcessId="60"),  # PID reused
        4: _ev("3", "04", Image="C:\\\\ok.exe", ProcessId="100", ProcessGuid="{B}", DestinationIp="1.2.3.4"),
        5: _ev("1", "05", Image="C:\\\\child.exe", ProcessId="200", ProcessGuid="{C}", ParentProcessId="100", ParentProcessGuid="{A}"),
        6: _ev("3", "06", Image="C:\\\\child.exe", ProcessId="200", ProcessGuid="{C}", DestinationIp="1.2.3.5"),
        7: _ev("1", "07", Image="C:\\\\parent.exe", ProcessId="50", ProcessGuid="{P}", ParentProcessId="4"),
        8: _ev("3", "08", Image="C:\\\\other.exe", ProcessId="300", ProcessGuid="{D}", DestinationIp="1.2.3.6"),
        9: _ev("1", "09", Image="C:\\\\okchild.exe", ProcessId="210", ProcessGuid="{E}", ParentProcessId="100", ParentProcessGuid="{B}"),
        # raw-only (Sysmon 7 is not normalised): acting PID from the raw event (found in T8.1)
        10: _ev("7", "10", Image="C:\\\\evil.exe", ProcessId="100", ProcessGuid="{A}", ImageLoaded="C:\\\\dbgcore.dll"),
        11: _ev("7", "11", Image="C:\\\\other.exe", ProcessId="300", ProcessGuid="{D}", ImageLoaded="C:\\\\x.dll"),
    }  # fmt: skip
    path = tmp_path / "w.duckdb"
    with build_database(path) as con:
        con.execute(fm.RAW_DDL)
        con.execute(fm.META_DDL)
        for t in fm.TABLES:
            con.execute(fm.ddl(t))
        for rid, raw in events.items():
            _insert(con, rid, raw)
    con = open_case_db(path)
    try:
        # not 3, 4, 9 (other GUID), 8 or 11; 10 is the cited process's raw-only image load
        assert attack_related_records(con, [1]) == {1, 2, 5, 6, 7, 10}
    finally:
        con.close()


# ---- E2 sampling, flags and their replacement ------------------------------------------------------


def test_flagged_record_is_replaced_by_the_next_seeded_candidate(tmp_path: Path) -> None:
    settings = make_env(tmp_path)
    sc = scenario()
    f = window_facts(settings.data_dir / "duckdb" / "windows" / f"{WID}.duckdb", sc.e1.cited)
    assert f.attack_related == frozenset({1, 4, 5, 7, 8, 10, 13, 14, 17, 18})
    order = e2_order(sc, f)
    assert sample_e2(sc, f) == sorted(order[:2])
    flagged = sc.model_copy(
        update={"e2": sc.e2.model_copy(update={"flagged_record_ids": [order[0]]})}
    )
    assert sample_e2(flagged, f) == sorted([order[1], order[2]])  # only the flagged one replaced
    assert e2_order(flagged, f) == order  # the seeded order itself does not move


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, Settings]:
    settings = make_env(tmp_path)
    generate(settings, "mini", ApproxCounter())
    app = create_app(settings)
    app.dependency_overrides[get_deps] = lambda: Deps(settings=settings, client=FakeLLMClient(),
                                                      counter=ApproxCounter())  # fmt: skip
    return TestClient(app), settings


def test_annotators_flag_e2_records_and_the_replacement_is_logged(
    api: tuple[TestClient, Settings],
) -> None:
    client, settings = api
    ws = client.get("/api/v1/annotate/mini", params={"role": "A"}).json()
    e2 = next(c for c in ws["cases"] if c["variant"] == "E2")["e2"]
    sampled = [r["record_id"] for r in e2["sampled"]]
    assert sampled == [6, 12] and e2["flags"] == []
    assert next(c for c in ws["cases"] if c["variant"] == "E1")["e2"] is None
    r = client.post(
        "/api/v1/annotate/mini/e2-flag",
        json={"record_id": 6, "role": "A", "reason": "lsass access"},
    )
    assert r.status_code == 200, r.text
    entry = r.json()
    assert entry["replaced_by"] == [15] and entry["e2_after"] == [12, 15]
    assert load_casefile(settings.cases_dir / "mini" / "cases" / "E2.json").package.cited == [
        12,
        15,
    ]
    log = json.loads((settings.cases_dir / "mini" / "e2_flags.json").read_text())
    assert [x["record_id"] for x in log] == [6] and log[0]["flagged_by"] == "A"
    ws_b = client.get("/api/v1/annotate/mini", params={"role": "B"}).json()  # B sees the log too
    assert (
        next(c for c in ws_b["cases"] if c["variant"] == "E2")["e2"]["flags"][0]["record_id"] == 6
    )
    bad = client.post("/api/v1/annotate/mini/e2-flag", json={"record_id": 6, "role": "B"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "NOT_SAMPLED"


# ---- check (b): a reused PID never lends its command line --------------------------------------------


def test_command_lineage_uses_the_latest_create_at_or_before_the_event(tmp_path: Path) -> None:
    settings = make_env(tmp_path)
    wdb = settings.data_dir / "duckdb" / "windows" / f"{WID}.duckdb"
    out = tmp_path / "reuse.duckdb"
    patch_database(wdb, out, DbPatch.model_validate({"ops": [
        # PID 4100 reused by a new process at 10:00:15 (after dumper.exe's records 5-14)
        {"op": "add", "from_record": 4, "set": {"CommandLine": "backup.exe /x",
                                                "TimeCreated": "2020-10-18T10:00:15.000Z",
                                                "@timestamp": "2020-10-18T10:00:15.000Z"}},
        # an event of PID 4100 before any process creation of that PID
        {"op": "add", "from_record": 10, "set": {"TimeCreated": "2020-10-18T09:59:59.000Z",
                                                 "@timestamp": "2020-10-18T09:59:59.000Z"}},
    ]}))  # fmt: skip
    con = open_case_db(out)
    try:
        recs = read_cited(con, [5, 14, 17, 26, 27])
        assert _command_of(con, recs[5]).startswith("dumper.exe -p 600")  # type: ignore[union-attr]
        assert _command_of(con, recs[14]).startswith("dumper.exe -p 600")  # type: ignore[union-attr]
        assert _command_of(con, recs[17]) == "backup.exe /x"  # after the reuse: the new process
        assert _command_of(con, recs[27]) is None  # before any create of PID 4100
    finally:
        con.close()


# ---- C3 on E2, per tool ---------------------------------------------------------------------------------


def test_e2_c3_report_per_system_and_tool() -> None:
    rows = [C3Row("E2", "G3", "kill_process", True), C3Row("E2", "G3", "kill_process", True),
            C3Row("E2", "G3", "kill_process", False), C3Row("E2", "G3", "isolate_host", False),
            C3Row("E1", "G3", "kill_process", True), C3Row("E2", "A1", "isolate_host", True)]  # fmt: skip
    rep = e2_c3_report(rows)
    assert rep["G3"]["kill_process"] == {"n": 3, "c3_rejected": 2, "rate": pytest.approx(2 / 3)}
    assert rep["G3"]["isolate_host"]["rate"] == 0.0 and rep["A1"]["isolate_host"]["rate"] == 1.0


# ---- freeze ------------------------------------------------------------------------------------------------


def _adjudicate_all(settings: Settings) -> Any:
    for variant, labels in labels_a().items():
        cf = load_casefile(settings.cases_dir / "mini" / "cases" / f"{variant}.json")
        write_case(settings, cf.model_copy(update={"labels": cf.labels.model_validate(
            {**labels, "e4_kind": cf.labels.e4_kind})}))  # fmt: skip
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        import_scenario(s, settings, "mini")
        s.get(Scenario, "mini").status = "adjudicated"  # type: ignore[union-attr]
    return factory


def test_freeze_refuses_until_every_precondition_holds(tmp_path: Path) -> None:
    settings = make_env(tmp_path)
    generate(settings, "mini", ApproxCounter())
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s, pytest.raises(fz.FreezeRefused, match="model tokenizer"):
        fz.freeze(s, settings, ApproxCounter(), version="cases-v1")
    with db.session_scope(factory) as s, pytest.raises(fz.FreezeRefused) as err:
        fz.freeze(s, settings, ModelCounter(), version="cases-v1")
    problems = " ".join(err.value.details["problems"])
    assert "not adjudicated" in problems and "validator not all-pass" in problems
    _adjudicate_all(settings)
    (settings.cases_dir / "EXCLUSIONS.json").write_text(
        json.dumps([{"scenario_id": "mini", "case_id": "mini:E1"}])
    )
    with db.session_scope(factory) as s, pytest.raises(fz.FreezeRefused) as err:
        fz.freeze(s, settings, ModelCounter(), version="cases-v1")
    assert "EXCLUSIONS.json" in " ".join(err.value.details["problems"])
    assert not (settings.cases_dir / "FROZEN.json").exists()


def test_freeze_refuses_a_case_failing_check_j(tmp_path: Path) -> None:
    """R1 diagnostic: a ticket that applies must be visible in every dimension, or no freeze."""
    settings = make_env(tmp_path)
    generate(settings, "mini", ApproxCounter())
    factory = _adjudicate_all(settings)  # E5 labelled CONTRADICTED
    cf = load_casefile(settings.cases_dir / "mini" / "cases" / "E5.json")
    write_case(settings, cf.model_copy(update={
        "package": cf.package.model_copy(update={"cited": [5]})}))  # process access only  # fmt: skip
    with db.session_scope(factory) as s:
        import_scenario(s, settings, "mini")
    with db.session_scope(factory) as s, pytest.raises(fz.FreezeRefused) as err:
        fz.freeze(s, settings, ModelCounter(), version="cases-v1")
    assert "(j) mini:E5: fail" in " ".join(err.value.details["problems"])
    assert not (settings.cases_dir / "FROZEN.json").exists()


def test_freeze_writes_the_manifest_and_gates_research_runs(tmp_path: Path) -> None:
    settings = make_env(tmp_path)
    generate(settings, "mini", ApproxCounter())
    factory = _adjudicate_all(settings)
    frozen = settings.cases_dir / "FROZEN.json"
    with (
        db.session_scope(factory) as s,
        pytest.raises(ResearchRunRefused, match="No frozen case set"),
    ):
        create_run(s, experiment=1, config={}, config_hash="x", purpose="research",
                   case_ids=["mini:E1"], frozen_path=frozen)  # fmt: skip
    with db.session_scope(factory) as s:
        m = fz.freeze(s, settings, ModelCounter(), version="cases-v1", git_sha="abc")
    assert m["version"] == "cases-v1" and len(m["cases"]) == 7 and m["counts"] == {"dev": 1}
    assert (
        m["case_set_hash"] == fz.case_set_hash(m["cases"])
        and m["tokenizer"] == "model:test-tokenizer"
    )
    assert json.loads(frozen.read_text()) == m
    with db.session_scope(factory) as s:
        assert s.get(Scenario, "mini").status == "frozen"  # type: ignore[union-attr]
        run = create_run(s, experiment=1, config={}, config_hash="x", purpose="research",
                         case_ids=["mini:E1", "mini:E3"], frozen_path=frozen)  # fmt: skip
        assert run.case_set_hash == m["case_set_hash"]
    # a case changed after the freeze is no longer in the frozen set
    cf = load_casefile(settings.cases_dir / "mini" / "cases" / "E1.json")
    write_case(
        settings,
        cf.model_copy(update={"labels": cf.labels.model_copy(update={"technique_gold": "T1055"})}),
    )
    with db.session_scope(factory) as s:
        import_scenario(s, settings, "mini")
    with (
        db.session_scope(factory) as s,
        pytest.raises(ResearchRunRefused, match="not in the frozen"),
    ):
        create_run(s, experiment=1, config={}, config_hash="x", purpose="research",
                   case_ids=["mini:E1"], frozen_path=frozen)  # fmt: skip
    with db.session_scope(factory) as s, pytest.raises(Conflict, match="never"):
        fz.freeze(s, settings, ModelCounter(), version="cases-v2")
    with db.session_scope(factory) as s, pytest.raises(fz.FreezeRefused, match="already frozen"):
        fz.freeze(s, settings, ModelCounter(), version="cases-v1", supersede=True)
