"""T1.5: eligibility, signature normalisation, Jaccard grouping."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gbya.data.build_db import build_database
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.dedup import (
    JACCARD_THRESHOLD,
    UnionFind,
    eligibility,
    group_windows,
    jaccard,
    normalise_text,
    signatures,
)
from gbya.data.fieldmap import TABLES, ddl
from gbya.data.normalise import normalise_window

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"


def _sig(i: int) -> tuple[int, str, str, str, str]:
    return (1, f"img{i}.exe", "", "", "")


def _set(ids: range) -> set[tuple[int, str, str, str, str]]:
    return {_sig(i) for i in ids}


def test_jaccard_boundary_60_grouped_40_not() -> None:
    a60, b60 = _set(range(0, 8)), _set(range(2, 10))  # 6 shared of 10
    a40, b40 = _set(range(0, 7)), _set(range(3, 10))  # 4 shared of 10
    assert jaccard(a60, b60) == pytest.approx(0.6) and jaccard(a40, b40) == pytest.approx(0.4)
    groups, _ = group_windows({"W1": a60, "W2": b60, "W3": _set(range(200, 210))})
    assert groups["W1"] == groups["W2"] == "W1" and groups["W3"] == "W3"
    groups40, _ = group_windows({"X1": a40, "X2": b40})
    assert groups40 == {"X1": "X1", "X2": "X2"}


def test_threshold_is_strict() -> None:
    a, b = _set(range(0, 6)), _set(range(2, 8))  # 4 shared of 8, J = 0.5
    assert jaccard(a, b) == JACCARD_THRESHOLD
    groups, pairs = group_windows({"A": a, "B": b})
    assert groups == {"A": "A", "B": "B"} and pairs == []


def test_union_find_is_transitive_and_uses_smallest_id() -> None:
    s1, s2, s3 = _set(range(0, 10)), _set(range(1, 11)), _set(range(2, 12))
    groups, _ = group_windows({"C": s3, "B": s2, "A": s1, "Z": _set(range(50, 60))})
    assert groups == {"A": "A", "B": "A", "C": "A", "Z": "Z"}


def test_empty_sets_are_not_similar() -> None:
    assert jaccard(set(), set()) == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C:\\Windows\\System32\\CMD.EXE", "c:\\windows\\system32\\cmd.exe"),
        ("rundll32 {3F2504E0-4F89-11D3-9A0C-0305E82C3301}", "rundll32 <guid>"),
        ("access 0x1FFFFF to 0x7ffe1234", "access <hex> to <hex>"),
        ("dump 600 123456 to x", "dump 600 <n> to x"),
        (
            "C:\\Users\\a\\AppData\\Local\\Temp\\tmp9x2.ps1 -x",
            "c:\\users\\a\\appdata\\local\\temp\\<tmp> -x",
        ),
        ("C:\\Windows\\Temp\\abc\\run.exe", "c:\\windows\\temp\\<tmp>\\run.exe"),
        (None, ""),
    ],
)
def test_normalise_text(raw: str | None, expected: str) -> None:
    assert normalise_text(raw) == expected


@given(
    st.dictionaries(
        st.sampled_from([f"W{i}" for i in range(8)]),
        st.sets(st.integers(0, 12), max_size=10),
        min_size=1,
    )
)
@settings(max_examples=150, deadline=None)
def test_groups_are_connected_components(raw: dict[str, set[int]]) -> None:
    sigs = {w: {_sig(i) for i in s} for w, s in raw.items()}
    groups, pairs = group_windows(sigs)
    # every linked pair shares a group; a group id is its smallest member
    for p in pairs:
        assert groups[p.a] == groups[p.b]
    for gid in set(groups.values()):
        assert gid == min(w for w, g in groups.items() if g == gid)
    # two windows share a group only if a chain of J > 0.5 links them
    uf = UnionFind(sigs)
    for p in pairs:
        uf.union(p.a, p.b)
    assert all(uf.find(w) == groups[w] for w in sigs)


@pytest.fixture(scope="module")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d") / "mini.duckdb"
    meta = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    normalise_window(parse_metadata(meta, MINI), MINI, out)
    return out


def test_eligibility_requires_30_events(mini_db: Path) -> None:
    con = open_case_db(mini_db)
    try:
        e = eligibility("SDWIN-MINI-000001", con)
    finally:
        con.close()
    assert e.events == 25 and not e.eligible and "only 25 events" in e.reason
    assert (
        e.primary_host == "WKSTN-01.lab.local" and e.primary_host_process_events == 7
    )  # the Winlogbeat event is on HR001


def test_eligibility_requires_process_events_on_primary_host(tmp_path: Path) -> None:
    path = tmp_path / "noproc.duckdb"
    with build_database(path) as con:
        con.execute(
            "CREATE TABLE raw_events (record_id BIGINT, channel VARCHAR, event_id INT, "
            "json VARCHAR)"
        )
        for t in TABLES:
            con.execute(ddl(t))
        con.execute(
            "INSERT INTO raw_events SELECT i, 'Security', 4624, '{}' FROM range(1, 41) t(i)"
        )
        con.execute(
            "INSERT INTO logon (record_id, host, event_id) "
            "SELECT i, 'dc-01', 4624 FROM range(1, 41) t(i)"
        )
    con = open_case_db(path)
    try:
        e = eligibility("W", con)
    finally:
        con.close()
    assert not e.eligible and e.primary_host == "dc-01" and "no process_create" in e.reason


def test_signatures_are_deterministic(mini_db: Path) -> None:
    c1, c2 = open_case_db(mini_db), open_case_db(mini_db)
    try:
        s1, s2 = signatures(c1), signatures(c2)
    finally:
        c1.close()
        c2.close()
    assert s1 == s2 and len(s1) > 10
    assert (10, "c:\\tools\\dumper.exe", "", "", "c:\\windows\\system32\\lsass.exe") in s1
