"""T1.2 integration: the real OTRF checkout at d9d40ef (skipped when it has not been fetched)."""

from __future__ import annotations

import pytest

from gbya.data.catalogue import STATUS_MISSING_HOST_FILE, catalogue_checksum, scan
from gbya.data.fetch import default_dest

ROOT = default_dest()
pytestmark = pytest.mark.skipif(
    not (ROOT / "datasets/atomic/_metadata").is_dir(), reason="run `make data-fetch` first"
)


def test_real_catalogue_has_100_windows_with_techniques() -> None:
    entries = scan(ROOT)
    assert len(entries) == 100
    assert all(e.techniques for e in entries)
    assert all(e.id.startswith("SDWIN-") for e in entries)


def test_real_catalogue_rerun_gives_identical_checksum() -> None:
    assert catalogue_checksum(scan(ROOT)) == catalogue_checksum(scan(ROOT))


def test_lsass_window_present() -> None:
    entries = {e.title: e for e in scan(ROOT)}
    hits = [e for e in entries.values() if any("dumpert" in f for f in e.host_files)]
    assert hits, "cmd_lsass_memory_dumpert_syscalls window not catalogued"
    assert "T1003.001" in hits[0].techniques


def test_only_known_missing_host_file() -> None:
    missing = [e.id for e in scan(ROOT) if e.status == STATUS_MISSING_HOST_FILE]
    assert missing == ["SDWIN-230718150800"]  # recorded in STATUS.md (T1.2)
