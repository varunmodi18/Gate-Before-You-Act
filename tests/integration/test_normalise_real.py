"""T1.3 smoke test on the real LSASS window (plan §D.1 [V]: 118 events, 95 Sysmon + 23 Security)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from gbya.data.catalogue import scan
from gbya.data.fetch import default_dest
from gbya.data.normalise import normalise_window

ROOT = default_dest()
LSASS = "SDWIN-201018225619"  # cmd_lsass_memory_dumpert_syscalls
pytestmark = pytest.mark.skipif(
    not (ROOT / "datasets/atomic/_metadata").is_dir(), reason="run `make data-fetch` first"
)


def test_lsass_window(tmp_path: Path) -> None:
    entry = next(e for e in scan(ROOT) if e.id == LSASS)
    out = tmp_path / "lsass.duckdb"
    res = normalise_window(entry, ROOT, out)
    assert res.events == 118 and res.skipped_lines == 0 and res.status == "ingested"
    con = duckdb.connect(str(out), read_only=True)
    try:
        by_channel = dict(
            con.execute("select channel, count(*) from raw_events group by 1").fetchall()
        )
        assert by_channel == {"Microsoft-Windows-Sysmon/Operational": 95, "Security": 23}
        assert con.execute("select count(*) from process_access").fetchone() == (48,)
        assert (
            con.execute(
                "select count(*) from process_access where target_image ilike '%lsass.exe'"
            ).fetchone()[0]
            > 0
        )  # type: ignore[index]
        dumpert = con.execute(
            "select pid, ppid, user from process_create where image ilike '%Outflank-Dumpert.exe'"
        ).fetchall()
        assert set(dumpert) == {(6772, 3080, "wardog")}  # 4688 and Sysmon 1 agree
    finally:
        con.close()
    assert res.hosts == ["WORKSTATION5"]
