"""Team decisions before M6: E2's sampling basis is stored with the case, label changes never move
E2 silently (the validator warns; regeneration is explicit and logged), and file or registry
records naming an attack process's image (e.g. its prefetch file) are excluded from E2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gbya.cases.cli import e2_log_path, generate, regenerate_e2
from gbya.cases.models import load_casefile
from gbya.cases.patch import _insert
from gbya.cases.processes import attack_related_records, attack_set
from gbya.cases.store import import_scenario, write_case
from gbya.cases.validator import validate_scenario
from gbya.config import Settings
from gbya.data import fieldmap as fm
from gbya.data.build_db import build_database
from gbya.data.connection import open_case_db
from gbya.llm.tokens import ApproxCounter
from gbya.store import db
from gbya.store.models import Case, Scenario
from tests.integration.test_builder import make_env
from tests.integration.test_m4_followups import _ev

COUNTER = ApproxCounter()


def e2(settings: Settings) -> Any:
    return load_casefile(settings.cases_dir / "mini" / "cases" / "E2.json")


def label_e1_evidence(settings: Settings, evidence: list[int]) -> None:
    cf = load_casefile(settings.cases_dir / "mini" / "cases" / "E1.json")
    labels = cf.labels.model_copy(update={"evidence_counterfactual": evidence})
    write_case(settings, cf.model_copy(update={"labels": labels}))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = make_env(tmp_path)
    generate(s, "mini", COUNTER)
    return s


# ---- item 4: target paths naming an attack image --------------------------------------------------


def test_file_and_registry_records_naming_an_attack_image_are_excluded(tmp_path: Path) -> None:
    events = {
        1: _ev("1", "01", Image="C:\\\\Users\\\\x\\\\Dumper.exe", ProcessId="100",
               ProcessGuid="{A}", ParentProcessId="50", ParentImage="C:\\\\Windows\\\\cmd.exe"),
        # the prefetch service writes the dumper's prefetch file (record 94 in demo-lsass)
        2: _ev("11", "02", Image="C:\\\\Windows\\\\svchost.exe", ProcessId="900",
               TargetFilename="C:\\\\Windows\\\\Prefetch\\\\DUMPER.EXE-440DD2BD.pf"),
        # an unrelated process writes a registry value naming the dumper
        3: _ev("13", "03", Image="C:\\\\Windows\\\\explorer.exe", ProcessId="70",
               EventType="SetValue", TargetObject="HKU\\\\S-1\\\\Software\\\\x\\\\dumper.exe"),
        # an unrelated file write stays eligible
        4: _ev("11", "04", Image="C:\\\\Windows\\\\svchost.exe", ProcessId="900",
               TargetFilename="C:\\\\Windows\\\\Temp\\\\report.txt"),
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
        assert attack_set(con, [1]).images == ("cmd.exe", "dumper.exe")  # its image and parent's
        assert attack_related_records(con, [1]) == {1, 2, 3}
    finally:
        con.close()


# ---- item 3: the basis is stored, E2 stays put, warnings, explicit regeneration -----------------


def test_generation_stores_the_basis_with_the_e2_case(settings: Settings) -> None:
    case = e2(settings)
    basis = case.e2_basis
    assert basis is not None and basis.evidence_records == [4, 5]  # E1's citations, no labels yet
    assert "dumper.exe" in basis.attack_images and {p["pid"] for p in basis.attack_processes} >= {
        4100
    }
    assert not set(case.package.cited) & set(basis.excluded_record_ids)
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:  # stored in app.db too (file → db → file)
        row = s.get(Case, "mini:E2")
        assert row is not None and row.e2_basis == basis.model_dump(mode="json")
    for other in ("E1", "E3", "R_pos"):
        assert (
            load_casefile(settings.cases_dir / "mini" / "cases" / f"{other}.json").e2_basis is None
        )


def test_label_changes_never_move_e2_and_the_validator_warns(settings: Settings) -> None:
    before = e2(settings)
    assert before.package.cited == [6, 12]
    label_e1_evidence(
        settings, [4, 5, 6]
    )  # an annotator lists record 6 (svchost→lsass) as evidence
    generate(settings, "mini", COUNTER)  # e.g. the Studio's Generate button
    after = e2(settings)
    assert after.package.cited == before.package.cited and after.e2_basis == before.e2_basis
    rep = validate_scenario(settings, "mini", COUNTER)
    check = next(c for c in rep.checks if c.check == "e2")
    assert check.status == "warn" and "[6]" in check.message and "regenerate-e2" in check.message
    assert rep.valid  # a warning, not a failure


def test_new_evidence_that_excludes_nothing_passes(settings: Settings) -> None:
    label_e1_evidence(settings, [4, 5, 7])  # 7: the dumper again (already in the set)
    rep = validate_scenario(settings, "mini", COUNTER)
    assert next(c for c in rep.checks if c.check == "e2").status == "pass"


def test_regeneration_is_explicit_and_logged(settings: Settings) -> None:
    label_e1_evidence(settings, [4, 5, 6])
    entry = regenerate_e2(settings, "mini", "record 6 labelled as evidence", by="test")
    after = e2(settings)
    assert after.e2_basis is not None and after.e2_basis.evidence_records == [4, 5, 6]
    assert 6 not in after.package.cited and entry["e2_before"] == [6, 12]
    assert entry["e2_after"] == after.package.cited and entry["by"] == "test"
    log = json.loads(e2_log_path(settings, "mini").read_text())
    assert [e["reason"] for e in log] == ["record 6 labelled as evidence"]
    rep = validate_scenario(settings, "mini", COUNTER)
    assert next(c for c in rep.checks if c.check == "e2").status == "pass"


def test_regeneration_refuses_frozen_scenarios_and_empty_reasons(settings: Settings) -> None:
    with pytest.raises(SystemExit, match="reason"):
        regenerate_e2(settings, "mini", "  ")
    factory = db.make_sessionmaker(db.make_engine(settings.app_db_path))
    with db.session_scope(factory) as s:
        import_scenario(s, settings, "mini")
        row = s.get(Scenario, "mini")
        assert row is not None
        row.status = "frozen"
    with pytest.raises(SystemExit, match="frozen"):
        regenerate_e2(settings, "mini", "why")
