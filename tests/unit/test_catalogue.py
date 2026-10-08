"""T1.2: catalogue parsing on fixture metadata, and idempotent upsert."""

from __future__ import annotations

from pathlib import Path

import pytest

from gbya.data.catalogue import (
    STATUS_CATALOGUED,
    STATUS_MISSING_HOST_FILE,
    TACTIC_NAMES,
    CatalogueError,
    catalogue_checksum,
    parse_metadata,
    scan,
    upsert,
)
from gbya.store import db
from gbya.store.models import Window

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "otrf"
META = FIXTURE / "datasets/atomic/_metadata"


def test_single_technique_with_sub_technique() -> None:
    e = parse_metadata(META / "SDWIN-000000000001.yaml", FIXTURE)
    assert e.id == "SDWIN-000000000001"
    assert e.techniques == ["T1003.001"]
    assert e.tactics == ["TA0006"]
    assert e.host_files == ["datasets/atomic/windows/credential_access/host/fixture_lsass.zip"]
    assert e.status == STATUS_CATALOGUED


def test_no_sub_technique_and_network_files_ignored() -> None:
    e = parse_metadata(META / "SDWIN-000000000002.yaml", FIXTURE)
    assert e.techniques == ["T1112"]
    assert e.host_files == [
        "datasets/atomic/windows/defense_evasion/host/fixture_registry.tar.gz"
    ]  # the Network file is not used


def test_multiple_mappings_kept_and_missing_host_file_flagged() -> None:
    e = parse_metadata(META / "SDWIN-000000000003.yaml", FIXTURE)
    assert e.techniques == ["T1134.001", "T1134.002", "T1135"]  # all kept, in order
    assert e.tactics == ["TA0004", "TA0005", "TA0007"]  # de-duplicated
    assert len(e.host_files) == 1 and e.missing_files[0].endswith("part2_absent.zip")
    assert e.status == STATUS_MISSING_HOST_FILE


def test_id_must_match_file_name(tmp_path: Path) -> None:
    meta = tmp_path / "datasets/atomic/_metadata"
    meta.mkdir(parents=True)
    bad = meta / "SDWIN-1.yaml"
    bad.write_text("id: SDWIN-2\ntitle: x\nattack_mappings: []\nfiles: []\n")
    with pytest.raises(CatalogueError):
        parse_metadata(bad, tmp_path)


def test_checksum_is_stable_and_order_independent() -> None:
    entries = scan(FIXTURE)
    assert catalogue_checksum(entries) == catalogue_checksum(list(reversed(scan(FIXTURE))))


def test_tactic_names_cover_enterprise_tactics() -> None:
    assert TACTIC_NAMES["TA0006"] == "credential_access" and len(TACTIC_NAMES) == 14


def test_upsert_is_idempotent_and_keeps_later_fields(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    db.upgrade(path)
    factory = db.make_sessionmaker(db.make_engine(path))
    entries = scan(FIXTURE)
    with db.session_scope(factory) as s:
        assert upsert(s, entries) == (3, 0)
    with db.session_scope(factory) as s:  # a later step fills split and status
        w = s.get(Window, "SDWIN-000000000001")
        assert w is not None
        w.split, w.ingest_status, w.hosts = "test", "ingested", ["wkstn-01"]
    with db.session_scope(factory) as s:
        assert upsert(s, entries) == (0, 3)
    with db.session_scope(factory) as s:
        w = s.get(Window, "SDWIN-000000000001")
        assert w is not None
        assert (w.split, w.ingest_status, w.hosts) == ("test", "ingested", ["wkstn-01"])
        missing = s.get(Window, "SDWIN-000000000003")
        assert missing is not None and missing.ingest_status == STATUS_MISSING_HOST_FILE
        assert s.query(Window).count() == 3
