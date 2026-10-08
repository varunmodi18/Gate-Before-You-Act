"""Acting users per host, primary-host share, and the docs/splits.md renderer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gbya.data.build_db import build_database
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.dedup import eligibility
from gbya.data.fieldmap import TABLES, ddl
from gbya.data.normalise import normalise_window
from gbya.data.splits_doc import render
from gbya.data.window_stats import ActingUsers, acting_users, is_builtin

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
H, DC = "WKSTN-01.lab.local", "DC-01.lab.local"


@pytest.fixture(scope="module")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d") / "mini.duckdb"
    meta = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    normalise_window(parse_metadata(meta, MINI), MINI, out)
    return out


def test_acting_users_on_workstation(mini_db: Path) -> None:
    con = open_case_db(mini_db)
    try:
        a = acting_users(con, H)
    finally:
        con.close()
    # hand count: process_create 3, process_access 2, network 1, registry 2, file 2, logon 1
    assert a.records == 11
    assert a.by_table == {
        "file": 2,
        "logon": 1,  # 4648 on the workstation: subject_user
        "network": 1,
        "process_access": 2,
        "process_create": 3,
        "registry": 2,
    }
    assert a.accounts == {"a.mehta": 10, "system": 1}
    assert a.non_builtin == {"a.mehta": 10}


def test_acting_users_on_dc_use_logon_target_user(mini_db: Path) -> None:
    con = open_case_db(mini_db)
    try:
        a = acting_users(con, DC)
    finally:
        con.close()
    assert a.by_table == {"logon": 2, "share_access": 2}
    assert a.accounts == {"a.mehta": 2, "admin": 1, "svc_reports": 1}


@pytest.mark.parametrize(
    ("name", "builtin"),
    [
        ("system", True),
        ("NETWORK SERVICE", True),
        ("wkstn-01$", True),
        ("dwm-1", True),
        ("umfd-0", True),
        ("anonymous logon", True),
        ("a.mehta", False),
        ("svc_reports", False),
    ],
)
def test_is_builtin(name: str, builtin: bool) -> None:
    assert is_builtin(name) is builtin


def test_primary_host_share_and_host_specific_process_check(tmp_path: Path) -> None:
    """Host A has most events but no process events; host B has one: B is primary."""
    path = tmp_path / "w.duckdb"
    with build_database(path) as con:
        con.execute(
            "CREATE TABLE raw_events (record_id BIGINT, channel VARCHAR, event_id INT, "
            "json VARCHAR)"
        )
        for t in TABLES:
            con.execute(ddl(t))
        for i in range(1, 37):  # 36 events on host A (network), 4 on host B
            host = "A" if i <= 36 else "B"
            con.execute(
                "INSERT INTO raw_events VALUES (?, 'Security', 5156, ?)",
                [i, json.dumps({"Hostname": host})],
            )
            con.execute(
                "INSERT INTO network (record_id, host, event_id) VALUES (?, ?, 5156)", [i, host]
            )
        for i in range(37, 41):
            con.execute(
                "INSERT INTO raw_events VALUES (?, 'Security', 4688, ?)",
                [i, json.dumps({"Hostname": "B"})],
            )
        con.execute("INSERT INTO process_create (record_id, host, event_id) VALUES (37, 'B', 4688)")
    con = open_case_db(path)
    try:
        e = eligibility("W", con)
    finally:
        con.close()
    assert e.primary_host == "B" and e.primary_host_process_events == 1 and e.eligible
    assert e.events == 40 and e.primary_host_events == 4 and e.primary_host_share == 0.1


def test_old_winlogbeat_host_field_counts_for_share(mini_db: Path) -> None:
    con = open_case_db(mini_db)
    try:
        e = eligibility("SDWIN-MINI-000001", con)
    finally:
        con.close()
    # 25 events: 20 on the workstation, 4 on the DC, 1 old-Winlogbeat event on HR001
    assert e.primary_host == H and e.primary_host_events == 20 and e.primary_host_share == 0.8


def test_render_splits_doc() -> None:
    doc = {
        "seed": 2026,
        "otrf_commit": "d9d40ef",
        "targets": {"dev": 1, "test": 1, "e2e": 0},
        "method": "m",
        "tactic_coverage": {"dev": {"execution": 1}, "test": {"discovery": 1}},
        "splits": {"dev": ["W1"], "test": ["W2"], "e2e": [], "unused": ["W3"]},
        "windows": {
            "W1": {"split": "dev", "dedup_group": "W1", "primary_tactic": "TA0002",
                   "primary_host": "h1", "primary_host_share": 0.5, "events": 10},
            "W2": {"split": "test", "dedup_group": "W2", "primary_tactic": "TA0007",
                   "primary_host": "h|2", "primary_host_share": 1.0, "events": 20},
            "W3": {"split": "unused", "dedup_group": "W2", "primary_tactic": "TA0007",
                   "primary_host": "h3", "primary_host_share": 0.25, "events": 5},
        },
    }  # fmt: skip
    acting = {
        "W1": ActingUsers("h1", 0),
        "W2": ActingUsers("h|2", 3, {"logon": 3}, {"system": 1, "bob": 2}),
    }
    text = render(doc, {"W1": "One", "W2": "Two", "W3": "Three"}, acting, "abc", ["a note"])
    assert "| `W1` | One | execution | dev | — | h1 | 50% | 10 |" in text
    assert "| `W3` | Three | discovery | unused | `W2` | h3 | 25% | 5 |" in text  # pair shown
    assert "h\\|2" in text  # pipes escaped
    assert "| `W1` | dev | **no** | 0 | — | none |" in text
    assert "| `W2` | test | yes | 3 | logon 3 | `bob` (2) |" in text
    assert "1 of 2 dev+test windows name an acting user" in text and "- a note" in text
