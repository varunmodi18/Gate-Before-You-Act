"""T4.2: per-case database patcher (construction path) — E3 move, E4 remove/add, E5 in-scope
edit, time shift; raw events and normalised tables stay consistent; results are read-only."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gbya.cases.models import DbPatch
from gbya.cases.patch import PatchError, patch_database, shift_ts
from gbya.data import fieldmap as fm
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.normalise import normalise_window

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
H, Y = "WKSTN-01.lab.local", "HR001.lab.local"
SUSPICIOUS = [1, 4, 5, 7, 8, 10, 13, 14, 17, 18]


@pytest.fixture(scope="module")
def window(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("w") / "window.duckdb"
    normalise_window(parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI),
                     MINI, out)  # fmt: skip
    return out


def rows(db: Path) -> dict[int, dict[str, Any]]:
    """record id → {table, normalised columns, raw}."""
    con = open_case_db(db)
    try:
        out: dict[int, dict[str, Any]] = {}
        for rid, raw in con.execute("SELECT record_id, json FROM raw_events").fetchall():
            out[int(rid)] = {"table": None, "raw": json.loads(raw)}
        for t in fm.TABLES:
            cur = con.execute(f'SELECT * FROM "{t}"')
            cols = [d[0] for d in cur.description or []]
            for r in cur.fetchall():
                d = dict(zip(cols, r, strict=True))
                out[int(d["record_id"])] |= {"table": t, **d}
        return out
    finally:
        con.close()


def patch(window: Path, tmp: Path, ops: list[dict[str, Any]]) -> tuple[Path, dict[str, list[int]]]:
    out = tmp / "case.duckdb"
    rep = patch_database(window, out, DbPatch.model_validate({"ops": ops}))
    return out, rep.to_json()


def test_e3_moves_the_malicious_events_to_y_via_the_cli(window: Path, tmp_path: Path) -> None:
    spec = tmp_path / "patch.json"
    spec.write_text(json.dumps({"ops": [{"op": "move_host", "record_ids": SUSPICIOUS, "host": Y}]}))
    out = tmp_path / "e3.duckdb"
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    res = subprocess.run([sys.executable, "-m", "gbya.cases.patch", str(window), str(out), str(spec)],
                         capture_output=True, text=True, check=True, env=env)  # fmt: skip
    assert json.loads(res.stdout.strip().splitlines()[-1])["moved"] == SUSPICIOUS
    before, after = rows(window), rows(out)
    for rid in SUSPICIOUS:  # moved in the normalised table and in the raw event
        assert after[rid]["host"] == Y and after[rid]["raw"]["Hostname"] == Y
    on_target = {rid for rid, r in after.items() if r.get("host") == H}
    assert not on_target & set(SUSPICIOUS)  # check (c): no malicious event left on the target
    assert on_target and all(after[r] == before[r] for r in on_target)  # benign events unchanged
    assert stat.S_IMODE(out.stat().st_mode) == 0o444 and before == rows(window)  # window untouched


def test_e4_partial_chain_removes_records_everywhere(window: Path, tmp_path: Path) -> None:
    out, rep = patch(window, tmp_path, [{"op": "remove", "record_ids": [5, 7]}])
    after = rows(out)
    assert rep["removed"] == [5, 7] and 5 not in after and 7 not in after
    con = open_case_db(out)
    assert con.execute(
        "SELECT count(*) FROM process_access WHERE record_id IN (5, 7)"
    ).fetchone() == (0,)
    con.close()
    assert len(after) == len(rows(window)) - 2


def test_e4_contradiction_adds_a_record_with_the_next_id(window: Path, tmp_path: Path) -> None:
    out, rep = patch(window, tmp_path, [
        {"op": "add", "from_record": 4, "set": {"CommandLine": "dumper.exe --selftest"}},
    ])  # fmt: skip
    new = max(rows(window)) + 1
    after = rows(out)
    assert rep["added"] == [new] and after[new]["table"] == "process_create"
    assert after[new]["command_line"] == "dumper.exe --selftest"
    assert after[new]["raw"]["CommandLine"] == "dumper.exe --selftest" and after[new]["pid"] == 4100
    authored = {"Hostname": H, "Channel": "Security", "EventID": "4688", "TimeCreated": "2020-10-18T10:30:00Z",
                "NewProcessName": "C:\\Windows\\backup.exe", "CommandLine": "backup.exe /all",
                "NewProcessId": "0x10", "ProcessId": "0x4", "SubjectUserName": "a.mehta"}  # fmt: skip
    out2 = tmp_path / "e4b.duckdb"
    patch_database(window, out2, DbPatch.model_validate({"ops": [{"op": "add", "raw": authored}]}))
    added = rows(out2)[new]
    assert (
        added["command_line"] == "backup.exe /all"
        and added["user"] == "a.mehta"
        and added["pid"] == 16
    )


def test_e5_edits_put_the_activity_inside_the_ticket(window: Path, tmp_path: Path) -> None:
    out, rep = patch(window, tmp_path, [
        {"op": "set_field", "record_ids": [1, 4], "key": "CommandLine", "value": "backup.exe /all"},
        {"op": "set_user", "record_ids": [14, 17], "user": "a.mehta"},
    ])  # fmt: skip
    after = rows(out)
    assert after[1]["command_line"] == after[4]["command_line"] == "backup.exe /all"
    assert after[4]["raw"]["CommandLine"] == "backup.exe /all"
    for rid in (14, 17):  # no user field before: the field the map reads is added
        assert after[rid]["user"] == "a.mehta" and after[rid]["raw"]["User"] == "a.mehta"
    assert sorted(rep["edited"]) == [1, 4, 14, 17]
    out2, _ = patch(
        window, tmp_path / "x", [{"op": "set_user", "record_ids": [4], "user": "b.khan"}]
    )
    assert (
        rows(out2)[4]["raw"]["User"] == "LAB\\b.khan" and rows(out2)[4]["user"] == "b.khan"
    )  # domain kept


def test_time_shift_keeps_formats_and_moves_the_normalised_ts(window: Path, tmp_path: Path) -> None:
    out, _ = patch(window, tmp_path, [{"op": "time_shift", "record_ids": [10], "seconds": 3600}])
    before, after = rows(window)[10], rows(out)[10]
    assert (after["ts"] - before["ts"]).total_seconds() == 3600
    assert after["raw"]["TimeCreated"] == "2020-10-18T11:00:09.000Z"
    assert after["raw"]["@timestamp"] == "2020-10-18T11:00:09.000Z"


@pytest.mark.parametrize(
    ("value", "seconds", "expected"),
    [
        ("2020-10-18T10:00:01.000Z", 60, "2020-10-18T10:01:01.000Z"),
        ("2020-10-18 23:59:59.1234567", 1, "2020-10-19 00:00:00.1234567"),
        ("2020-10-18T10:00:00+02:00", -3600, "2020-10-18T09:00:00+02:00"),
        ("2020-10-18 10:00:00", 0, "2020-10-18 10:00:00"),
    ],
)
def test_shift_ts_formats(value: str, seconds: int, expected: str) -> None:
    assert shift_ts(value, seconds) == expected


def test_a_failed_patch_leaves_no_file(window: Path, tmp_path: Path) -> None:
    out = tmp_path / "bad.duckdb"
    with pytest.raises(PatchError, match="record 999 does not exist"):
        patch_database(window, out, DbPatch.model_validate(
            {"ops": [{"op": "remove", "record_ids": [5]}, {"op": "remove", "record_ids": [999]}]}))  # fmt: skip
    assert not out.exists() and not list(tmp_path.glob("*.patching*"))
    with pytest.raises(PatchError, match="cannot shift"):
        shift_ts("18/10/2020 10:00", 5)
