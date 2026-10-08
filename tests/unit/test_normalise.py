"""T1.3: golden test of the normaliser on the hand-crafted mini window, archives, determinism."""

from __future__ import annotations

import json
import shutil
import stat
import tarfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from gbya.data import fieldmap as fm
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import is_event_member, normalise_window

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
META = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
HOST_JSON = "datasets/atomic/windows/credential_access/host/mini_window.json"
H, DC = "WKSTN-01.lab.local", "DC-01.lab.local"


def _read(path: Path, sql: str) -> list[tuple[Any, ...]]:
    # Tests read the built file directly; agent code uses gbya.data.connection (T1.3a).
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any]:
    out = tmp_path_factory.mktemp("db") / "mini.duckdb"
    res = normalise_window(parse_metadata(META, MINI), MINI, out)
    return out, res


def test_counts_and_status(built: tuple[Path, Any]) -> None:
    path, res = built
    assert res.events == 25 and res.skipped_lines == 2  # one malformed line, one bare number
    assert res.status == "ingest_warning"  # 2 / 27 lines skipped > 1%
    assert res.table_rows == {
        "process_create": 4,
        "process_access": 4,
        "network": 3,
        "registry": 4,
        "file": 3,
        "logon": 3,
        "share_access": 2,
    }
    assert _read(path, "select count(*) from raw_events") == [(25,)]
    assert res.hosts == [H, DC, "HR001.lab.local"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o444


def test_record_ids_follow_timestamp_then_line(built: tuple[Path, Any]) -> None:
    path, _ = built
    rows = _read(path, "select record_id, event_id from raw_events order by record_id")
    # The 4688 at 10:00:01 is line 2 of the file but sorts first; the two Sysmon 10 events
    # share a timestamp and keep their file order (dumper.exe before svchost.exe).
    assert rows[0] == (1, 4688)
    pa = _read(path, "select record_id, source_image from process_access order by record_id")
    assert pa[0] == (5, "C:\\Tools\\dumper.exe") and pa[1] == (6, "C:\\Windows\\svchost.exe")
    assert _read(path, "select max(record_id) from raw_events") == [(25,)]


def test_process_create_fields(built: tuple[Path, Any]) -> None:
    path, _ = built
    rows = {
        r[0]: r[1:]
        for r in _read(
            path,
            "select record_id, event_id, image, pid, ppid, user, integrity_level, hashes,"
            " length(command_line) from process_create order by record_id",
        )
    }
    assert rows[1] == (4688, "C:\\Tools\\dumper.exe", 4100, 3080, "a.mehta", "High", None, 10)
    assert rows[2][2] == 4242  # nxlog, no ProcessId: read from the event's own Message
    assert rows[3][2] is None and rows[3][4] is None  # no ProcessId, empty Message; "-" user → NULL
    # Sysmon 1: DOMAIN\\ stripped and lower-cased; the long command line is stored in full.
    assert rows[4][:6] == (1, "C:\\Tools\\dumper.exe", 4100, 3080, "a.mehta", "High")
    assert rows[4][7] == len("dumper.exe -p 600 " + "A" * 3000 + " DECISIVE")
    assert _read(path, "select count(*) from process_create where pid = 9999") == [(0,)]


def test_process_access_fields(built: tuple[Path, Any]) -> None:
    path, _ = built
    rows = _read(
        path,
        "select channel, event_id, source_pid, target_image, target_pid, granted_access, user, host"
        " from process_access order by record_id",
    )
    assert rows[0] == (
        fm.CANONICAL_CHANNELS[fm.SYSMON],
        10,
        4100,
        "C:\\Windows\\system32\\lsass.exe",
        600,
        "0x1FFFFF",
        "a.mehta",
        H,
    )
    assert rows[1][6] is None  # no SourceUser in this Sysmon version
    assert rows[2][:3] == ("Security", 4656, 4100) and rows[2][4] is None  # hex PID; no target PID
    assert rows[3][2] == 77 and rows[3][7] == "HR001.lab.local"  # old Winlogbeat layout


def test_network_fields_and_pid_sources(built: tuple[Path, Any]) -> None:
    path, res = built
    rows = _read(
        path,
        "select channel, pid, dst_ip, dst_port, direction, user from network order by record_id",
    )
    assert rows[0] == (
        fm.CANONICAL_CHANNELS[fm.SYSMON],
        4100,
        "203.0.113.7",
        443,
        "outbound",
        "a.mehta",
    )
    assert rows[1] == (
        "Security",
        716,
        "203.0.113.8",
        80,
        "outbound",
        None,
    )  # nxlog ProcessID=4 ignored
    assert rows[2] == (
        "Security",
        3488,
        "10.0.0.5",
        445,
        "inbound",
        None,
    )  # 'security' canonicalised
    assert res.pid_from_message == 2  # Sysmon 1 (4242) and 5156 (716)


def test_registry_file_logon_share(built: tuple[Path, Any]) -> None:
    path, _ = built
    reg = _read(
        path,
        "select event_id, event_type, pid, target_object, details, user"
        " from registry order by record_id",
    )
    assert [r[0] for r in reg] == [12, 13, 14, 4657]
    assert reg[2][4] == "HKLM\\SOFTWARE\\Evil2"  # Sysmon 14 NewName
    assert reg[3] == (
        4657,
        "%%1905",
        42,
        "\\REGISTRY\\MACHINE\\SYSTEM\\CurrentControlSet\\Control\\Lsa\\RunAsPPL",
        "0",
        "a.mehta",
    )
    files = _read(
        path, "select event_id, event_type, pid, target_filename, user from file order by record_id"
    )
    assert files == [
        (4663, "%%4417", 4100, "C:\\Temp\\lsass.dmp", "a.mehta"),
        (11, "FileCreate", 4100, "C:\\Temp\\lsass.dmp", None),
        (23, "FileDelete", 4100, "C:\\Temp\\lsass.dmp", "a.mehta"),
    ]
    logon = _read(
        path,
        "select event_id, subject_user, target_user, logon_type, src_ip, workstation,"
        " process_name, host from logon order by record_id",
    )
    assert logon[0] == (4624, None, "svc_reports", 3, "10.0.0.5", "WKSTN-01", None, DC)
    assert logon[2] == (
        4648,
        "a.mehta",
        "svc_reports",
        None,
        None,
        None,
        "C:\\Windows\\System32\\runas.exe",
        H,
    )
    share = _read(
        path,
        "select event_id, subject_user, share_name, relative_target"
        " from share_access order by record_id",
    )
    assert share == [
        (5140, "a.mehta", "\\\\*\\C$", None),
        (5145, "a.mehta", "\\\\*\\C$", "Windows\\Temp\\x.exe"),
    ]


def test_unmapped_and_non_process_objects_only_in_raw(built: tuple[Path, Any]) -> None:
    path, _ = built
    ids = {r[0] for r in _read(path, "select event_id from raw_events")}
    assert {7, 4663} <= ids
    mapped = sum(_read(path, f'select count(*) from "{t}"')[0][0] for t in fm.TABLES)
    assert mapped == 23  # 25 events minus Sysmon 7 and the 4663 on a registry key


def test_raw_json_is_the_original_event(built: tuple[Path, Any]) -> None:
    path, _ = built
    (raw,) = _read(path, "select json from raw_events where record_id = 25")[0]
    assert json.loads(raw)["event_data"]["SourceProcessId"] == "77"  # unflattened original
    (ts,) = _read(path, "select ts from process_access where record_id = 25")[0]
    assert ts == datetime(2020, 10, 18, 10, 0, 24)


def test_meta_table(built: tuple[Path, Any]) -> None:
    path, _ = built
    meta = dict(_read(path, "select key, value from _meta"))
    assert meta["window_id"] == "SDWIN-MINI-000001"
    assert meta["ordering"] == "timestamp_then_line" and meta["skipped_lines"] == "2"
    assert json.loads(meta["source_files"]) == [HOST_JSON]


def test_rebuild_is_identical(built: tuple[Path, Any], tmp_path: Path) -> None:
    path, _ = built
    again = tmp_path / "again.duckdb"
    normalise_window(parse_metadata(META, MINI), MINI, again)
    for table in ["raw_events", *fm.TABLES]:
        q = f'select * from "{table}" order by record_id'
        assert _read(path, q) == _read(again, q), table


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_archives_give_the_same_result(kind: str, tmp_path: Path, built: tuple[Path, Any]) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(MINI, repo)
    src = repo / HOST_JSON
    archive = src.with_name("mini_window." + kind)
    if kind == "zip":
        with zipfile.ZipFile(archive, "w") as z:
            z.write(src, "mini_window.json")
            z.writestr("__MACOSX/._mini_window.json", b"\x00\x05\x16\x07 resource fork")
    else:
        with tarfile.open(archive, "w:gz") as t:
            t.add(src, "mini_window.json")
    src.unlink()
    meta = repo / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    meta.write_text(meta.read_text().replace("mini_window.json", "mini_window." + kind))
    res = normalise_window(parse_metadata(meta, repo), repo, tmp_path / "a.duckdb")
    assert res.events == 25 and res.skipped_lines == 2  # the __MACOSX member is not read
    q = "select record_id, json from raw_events order by record_id"
    assert _read(tmp_path / "a.duckdb", q) == _read(built[0], q)


def test_macos_debris_is_not_an_event_member() -> None:
    assert is_event_member("x/events.json")
    assert not is_event_member("__MACOSX/._events.json")
    assert not is_event_member("x/._events.json")
    assert not is_event_member("capture.cap")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2020-10-18T10:00:05.123Z", datetime(2020, 10, 18, 10, 0, 5, 123000)),
        ("2020-10-18T12:00:05+02:00", datetime(2020, 10, 18, 10, 0, 5)),
        ("2020-10-18 02:17:12.527", datetime(2020, 10, 18, 2, 17, 12, 527000)),
        ("2020-10-18 02:17:12", datetime(2020, 10, 18, 2, 17, 12)),
        ("not a time", None),
        (None, None),
    ],
)
def test_parse_ts(value: str | None, expected: datetime | None) -> None:
    assert fm.parse_ts(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("0x1004", 4100), ("4100", 4100), (4100, 4100), ("-", None), ("", None), ("x", None)],
)
def test_as_int(value: object, expected: int | None) -> None:
    assert fm.as_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("LAB\\A.Mehta", "a.mehta"),
        ("NT AUTHORITY\\SYSTEM", "system"),
        ("-", None),
        ("svc_x", "svc_x"),
    ],
)
def test_norm_user(value: str, expected: str | None) -> None:
    assert fm.norm_user(value) == expected
