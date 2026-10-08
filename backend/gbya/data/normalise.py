"""Normalise each window's events into a read-only DuckDB file (plan §D.1 step 3, T1.3).

For every catalogued window with its Host file(s) present:

1. read every JSON line of every ``.json`` member of the Host archive(s), in metadata order
   (lines that are not JSON objects are skipped and counted);
2. order events by (``TimeCreated``, else ``@timestamp``; original line number) and give each a
   1-based ``record_id``. If any event lacks a parseable timestamp, the original order is kept
   and the window is flagged;
3. store every event unchanged in ``raw_events`` and each mapped event in its table (§F.2,
   ``gbya.data.fieldmap``);
4. build ``data/duckdb/windows/<window_id>.duckdb`` on the construction path, then mode 0444;
5. update the catalogue row: host names, event count, DuckDB path and ingest status.

    python -m gbya.data.normalise [WINDOW_ID ...]      # make normalise
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from gbya.data import fieldmap as fm
from gbya.data.build_db import build_database
from gbya.data.catalogue import STATUS_MISSING_HOST_FILE, WindowEntry
from gbya.data.fetch import OTRF_COMMIT
from gbya.data.hosts import host_counts, hosts_by_frequency
from gbya.logging import get_logger

log = get_logger("gbya.data.normalise")

STATUS_INGESTED = "ingested"
STATUS_INGEST_WARNING = "ingest_warning"
SKIP_WARNING_RATE = 0.01  # §D.1: more than 1% skipped lines → ingest_warning
LARGE_WINDOW = 200_000  # §D.1: warn above this many events


@dataclass
class IngestResult:
    window_id: str
    path: Path
    events: int = 0
    skipped_lines: int = 0
    missing_ts: int = 0
    table_rows: dict[str, int] = field(default_factory=dict)
    ts_source: dict[str, int] = field(default_factory=dict)
    pid_from_message: int = 0
    hosts: list[str] = field(default_factory=list)
    status: str = STATUS_INGESTED

    def meta(self, entry: WindowEntry) -> dict[str, str]:
        return {
            "window_id": self.window_id,
            "otrf_commit": OTRF_COMMIT,
            "source_files": json.dumps(entry.host_files),
            "events": str(self.events),
            "skipped_lines": str(self.skipped_lines),
            "missing_ts": str(self.missing_ts),
            "ordering": "original" if self.missing_ts else "timestamp_then_line",
            "ts_source": json.dumps(self.ts_source, sort_keys=True),
            "pid_from_message": str(self.pid_from_message),
            "table_rows": json.dumps(self.table_rows, sort_keys=True),
            "hosts": json.dumps(self.hosts),
        }


def is_event_member(name: str) -> bool:
    """A ``.json`` archive member that holds events.

    macOS archive debris (``__MACOSX/`` and AppleDouble ``._*`` resource forks, found in 14 OTRF
    zips) is not event data and is excluded; ``.cap`` and other members are ignored.
    """
    base = name.rsplit("/", 1)[-1]
    return name.endswith(".json") and not name.startswith("__MACOSX/") and not base.startswith("._")


def _json_members(path: Path) -> Iterator[tuple[str, bytes]]:
    """Event members of a Host file, in name order."""
    if path.name.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if is_event_member(name):
                    yield name, z.read(name)
    elif path.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, "r:gz") as t:
            for m in sorted(t.getmembers(), key=lambda m: m.name):
                f = t.extractfile(m) if m.isfile() and is_event_member(m.name) else None
                if f is not None:
                    yield m.name, f.read()
    elif path.name.endswith(".json"):
        yield path.name, path.read_bytes()
    else:
        raise ValueError(f"unsupported Host file type: {path.name}")


def read_events(repo_root: Path, host_files: list[str]) -> tuple[list[dict[str, Any]], int]:
    """All JSON-object lines of the window, in file/line order, and the count of skipped lines."""
    events: list[dict[str, Any]] = []
    skipped = 0
    for rel in host_files:
        for member, data in _json_members(repo_root / rel):
            text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="replace")
            for line_no, line in enumerate(text, start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    log.warning(
                        "skipped_line", file=rel, member=member, line=line_no, reason="json"
                    )
                    skipped += 1
                    continue
                if not isinstance(obj, dict):
                    log.warning(
                        "skipped_line", file=rel, member=member, line=line_no, reason="not_object"
                    )
                    skipped += 1
                    continue
                events.append(obj)
    return events, skipped


def _frame(table: str, rows: list[dict[str, Any]]) -> pd.DataFrame:
    cols = fm.COMMON_COLUMNS + fm.TABLE_COLUMNS[table]
    data: dict[str, Any] = {}
    for name, sql_type in cols:
        values = [r.get(name) for r in rows]
        if sql_type.startswith(("BIGINT", "INTEGER")):
            data[name] = pd.array(values, dtype="Int64")
        elif sql_type == "TIMESTAMP":
            data[name] = pd.to_datetime(pd.Series(values, dtype="object"))
        else:
            data[name] = pd.Series(values, dtype="object")
    return pd.DataFrame(data)


RawRow = tuple[int, str | None, int | None, str]


def derive_record(
    record_id: int, raw: dict[str, Any]
) -> tuple[RawRow, str | None, dict[str, Any] | None]:
    """One raw event → its ``raw_events`` row and, if routed, its table and normalised row.

    The single derivation used by the normaliser and the case patcher (T4.2), so a patched
    record's normalised row always agrees with its raw JSON."""
    canon = fm.canonical(raw)
    channel, event_id = fm.channel_of(canon), fm.event_id_of(canon)
    raw_row: RawRow = (record_id, channel, event_id, fm.raw_json(raw))
    routed = fm.route(canon)
    if routed is None:
        return raw_row, None, None
    table, extract = routed
    row: dict[str, Any] = {
        "record_id": record_id,
        "ts": fm.parse_ts(fm.timestamp_source(canon)[1]),
        "host": fm.host_of(canon),
        "channel": channel,
        "event_id": event_id,
    }
    row.update(extract(canon))
    return raw_row, table, row


def normalise_window(entry: WindowEntry, repo_root: Path, out_path: Path) -> IngestResult:
    result = IngestResult(entry.id, out_path)
    raw_events, result.skipped_lines = read_events(repo_root, entry.host_files)

    prepared: list[tuple[datetime | None, int, dict[str, Any], dict[str, Any]]] = []
    ts_source: Counter[str] = Counter()
    for line_idx, raw in enumerate(raw_events):
        canon = fm.canonical(raw)
        key, value = fm.timestamp_source(canon)
        ts = fm.parse_ts(value)
        ts_source[key or "none"] += 1
        prepared.append((ts, line_idx, raw, canon))
    result.missing_ts = sum(1 for ts, *_ in prepared if ts is None)
    if result.missing_ts == 0:
        prepared.sort(key=lambda p: (p[0], p[1]))

    raw_rows: list[tuple[int, str | None, int | None, str]] = []
    table_rows: dict[str, list[dict[str, Any]]] = {t: [] for t in fm.TABLES}
    for record_id, (_ts, _line, raw, canon) in enumerate(prepared, start=1):
        raw_row, table, row = derive_record(record_id, raw)
        raw_rows.append(raw_row)
        if table is None or row is None:
            continue
        if fm.pid_from_message(canon, row.get("pid")):
            result.pid_from_message += 1
        table_rows[table].append(row)

    result.events = len(raw_rows)
    result.ts_source = dict(sorted(ts_source.items()))
    result.table_rows = {t: len(rows) for t, rows in table_rows.items()}
    result.hosts = hosts_by_frequency(host_counts(fm.canonical(e) for e in raw_events))
    total_lines = result.events + result.skipped_lines
    if total_lines and result.skipped_lines / total_lines > SKIP_WARNING_RATE:
        result.status = STATUS_INGEST_WARNING
    if result.events > LARGE_WINDOW:
        log.warning("large_window", window=entry.id, events=result.events)

    with build_database(out_path) as con:
        con.execute(fm.RAW_DDL)
        con.execute(fm.META_DDL)
        for table in fm.TABLES:
            con.execute(fm.ddl(table))
        if raw_rows:
            raw_df = pd.DataFrame(
                {
                    "record_id": pd.array([r[0] for r in raw_rows], dtype="Int64"),
                    "channel": pd.Series([r[1] for r in raw_rows], dtype="object"),
                    "event_id": pd.array([r[2] for r in raw_rows], dtype="Int64"),
                    "json": pd.Series([r[3] for r in raw_rows], dtype="object"),
                }
            )
            con.register("_raw_df", raw_df)
            con.execute("INSERT INTO raw_events SELECT * FROM _raw_df")
            con.unregister("_raw_df")
        for table, rows in table_rows.items():
            if not rows:
                continue
            df = _frame(table, rows)
            con.register("_df", df)
            cols = ", ".join(f'"{c}"' for c, _ in fm.COMMON_COLUMNS + fm.TABLE_COLUMNS[table])
            con.execute(f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM _df')
            con.unregister("_df")
        con.executemany("INSERT INTO _meta VALUES (?, ?)", sorted(result.meta(entry).items()))
    log.info(
        "window_normalised",
        window=entry.id,
        events=result.events,
        skipped=result.skipped_lines,
        status=result.status,
        tables=result.table_rows,
    )
    return result


def window_db_path(data_dir: Path, window_id: str) -> Path:
    return data_dir / "duckdb" / "windows" / f"{window_id}.duckdb"


def main(argv: list[str] | None = None) -> None:
    import sys

    from gbya.config import REPO_ROOT, get_settings
    from gbya.data.catalogue import scan
    from gbya.data.fetch import default_dest
    from gbya.logging import configure_logging
    from gbya.store.db import make_engine, make_sessionmaker, session_scope, upgrade
    from gbya.store.models import Window

    args = sys.argv[1:] if argv is None else argv
    settings = get_settings()
    configure_logging("data", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    repo_root = default_dest()
    data_dir = settings.resolve(settings.data_dir)
    entries = [e for e in scan(repo_root) if not args or e.id in args]
    upgrade()
    factory = make_sessionmaker(make_engine())
    statuses: Counter[str] = Counter()
    for entry in entries:
        if entry.status == STATUS_MISSING_HOST_FILE:
            statuses[STATUS_MISSING_HOST_FILE] += 1
            continue
        out = window_db_path(data_dir, entry.id)
        res = normalise_window(entry, repo_root, out)
        statuses[res.status] += 1
        with session_scope(factory) as session:
            row = session.get(Window, entry.id)
            if row is None:
                raise SystemExit(f"{entry.id} is not catalogued; run `make catalogue` first")
            row.hosts = res.hosts
            row.event_count = res.events
            row.duckdb_path = (
                str(out.relative_to(REPO_ROOT)) if out.is_relative_to(REPO_ROOT) else str(out)
            )
            row.ingest_status = res.status
        print(
            f"{entry.id}: {res.events} events, {res.skipped_lines} skipped, {res.status}, "
            f"hosts={res.hosts[:3]}"
        )
    print(f"windows by status: {dict(statuses)}")


if __name__ == "__main__":
    main()
