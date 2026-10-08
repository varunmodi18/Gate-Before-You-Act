"""Per-case database patcher for E3/E4/E5 (plan §D.11, §D.1.1, T4.2). CONSTRUCTION PATH.

Runs only as a command-line subprocess (never inside the API or the worker):

    python -m gbya.cases.patch <window.duckdb> <case.duckdb> <patch.json>

The window database is copied, the operations are applied in order, and the copy is made
read-only (mode 0444) before it appears under its final name (``build_db.patch_copy``).

Every operation edits the record's **raw event** and then re-derives the record's ``raw_events``
row and normalised row with the normaliser's own ``derive_record``, so all tables and
``raw_events`` stay consistent by construction:

* ``move_host`` — the raw host fields (``Hostname``/``Computer``/``computer_name``);
* ``remove`` — the record disappears from ``raw_events`` and its table;
* ``add`` — a new record (``record_id`` = max + 1) from an authored raw event or a copy of
  record N with raw fields set;
* ``time_shift`` — every timestamp field, shifted by whole seconds in its original format;
* ``set_user`` — the raw field from which the record's acting user is derived (added if absent);
* ``set_field`` — any raw field.

After each edit the derived row is checked (host, acting user, timestamp), so an edit that the
field map would not reflect is an error, never a silent mismatch.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from gbya.cases.models import Add, DbPatch, MoveHost, Remove, SetField, SetUser, TimeShift
from gbya.context.models import account_key, host_key
from gbya.data import fieldmap as fm
from gbya.data.build_db import patch_copy
from gbya.data.normalise import derive_record

HOST_KEYS = ("Hostname", "Computer", "computer_name")
TS_KEYS = ("TimeCreated", "@timestamp", "EventTime", "UtcTime", "EventReceivedTime")
USER_KEYS = ("User", "SourceUser", "TargetUserName", "SubjectUserName", "AccountName")
_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})([T ])(\d{2}:\d{2}:\d{2})(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


class PatchError(RuntimeError):
    pass


@dataclass
class PatchReport:
    moved: list[int] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)
    edited: list[int] = field(default_factory=list)

    def to_json(self) -> dict[str, list[int]]:
        return {"moved": self.moved, "removed": self.removed, "added": self.added,
                "edited": sorted(set(self.edited))}  # fmt: skip


# ---- raw-event helpers -------------------------------------------------------------------------


def _containers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The flat event, or the old Winlogbeat layout's top level and ``event_data``."""
    inner = raw.get("event_data")
    return [raw, inner] if isinstance(inner, dict) else [raw]


def _has(raw: dict[str, Any], key: str) -> bool:
    return any(key in c for c in _containers(raw))


def _set(raw: dict[str, Any], key: str, value: Any) -> None:
    for c in _containers(raw):
        if key in c:
            c[key] = value
            return
    _containers(raw)[-1][key] = value  # new key: flat event, or event_data in the old layout


def shift_ts(value: str, seconds: int) -> str:
    """Shift a timestamp string by whole seconds, keeping its exact format (fraction, suffix)."""
    m = _TS.match(value.strip())
    if m is None:
        raise PatchError(f"cannot shift timestamp {value!r}")
    date, sep, time, frac, suffix = m.groups()
    dt = datetime.fromisoformat(f"{date}T{time}") + timedelta(seconds=seconds)
    return f"{dt:%Y-%m-%d}{sep}{dt:%H:%M:%S}{frac or ''}{suffix or ''}"


def _load_raw(con: duckdb.DuckDBPyConnection, rid: int) -> dict[str, Any]:
    row = con.execute("SELECT json FROM raw_events WHERE record_id = ?", [rid]).fetchone()
    if row is None:
        raise PatchError(f"record {rid} does not exist")
    raw: dict[str, Any] = json.loads(row[0])
    return raw


def _delete(con: duckdb.DuckDBPyConnection, rid: int) -> None:
    con.execute("DELETE FROM raw_events WHERE record_id = ?", [rid])
    for table in fm.TABLES:
        con.execute(f'DELETE FROM "{table}" WHERE record_id = ?', [rid])


def _insert(con: duckdb.DuckDBPyConnection, rid: int, raw: dict[str, Any]) -> dict[str, Any] | None:
    raw_row, table, row = derive_record(rid, raw)
    con.execute("INSERT INTO raw_events VALUES (?, ?, ?, ?)", list(raw_row))
    if table is None or row is None:
        return None
    cols = [c for c, _ in fm.COMMON_COLUMNS + fm.TABLE_COLUMNS[table]]
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    con.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', [row.get(c) for c in cols])
    return {**row, "_table": table}


def _rewrite(
    con: duckdb.DuckDBPyConnection, rid: int, raw: dict[str, Any]
) -> dict[str, Any] | None:
    _delete(con, rid)
    return _insert(con, rid, raw)


def _acting_user(row: dict[str, Any] | None) -> str | None:
    if row is None:
        return None
    v = row.get(fm.acting_user_column(row["_table"], int(row.get("event_id") or 0)))
    return account_key(str(v)) if v else None


# ---- operations -------------------------------------------------------------------------------


def _move_host(con: duckdb.DuckDBPyConnection, op: MoveHost, rep: PatchReport) -> None:
    for rid in op.record_ids:
        raw = _load_raw(con, rid)
        keys = [k for k in HOST_KEYS if _has(raw, k)] or ["Hostname"]
        for k in keys:
            _set(raw, k, op.host)
        row = _rewrite(con, rid, raw)
        derived = fm.host_of(fm.canonical(raw))
        if derived is None or host_key(derived) != host_key(op.host):
            raise PatchError(f"record {rid}: host is {derived!r} after move_host")
        if row is not None and host_key(str(row["host"])) != host_key(op.host):
            raise PatchError(f"record {rid}: normalised host did not change")
        rep.moved.append(rid)


def _remove(con: duckdb.DuckDBPyConnection, op: Remove, rep: PatchReport) -> None:
    for rid in op.record_ids:
        _load_raw(con, rid)  # must exist
        _delete(con, rid)
        rep.removed.append(rid)


def _add(con: duckdb.DuckDBPyConnection, op: Add, rep: PatchReport) -> int:
    base = copy.deepcopy(op.raw) if op.raw is not None else _load_raw(con, int(op.from_record or 0))
    for k, v in op.set.items():
        _set(base, k, v)
    row = con.execute("SELECT max(record_id) FROM raw_events").fetchone()
    rid = int((row[0] if row else None) or 0) + 1
    _insert(con, rid, base)
    rep.added.append(rid)
    return rid


def _time_shift(con: duckdb.DuckDBPyConnection, op: TimeShift, rep: PatchReport) -> None:
    for rid in op.record_ids:
        raw = _load_raw(con, rid)
        before = fm.parse_ts(fm.timestamp_source(fm.canonical(raw))[1])
        for c in _containers(raw):
            for k in TS_KEYS:
                if isinstance(c.get(k), str) and c[k].strip():
                    c[k] = shift_ts(c[k], op.seconds)
        _rewrite(con, rid, raw)
        after = fm.parse_ts(fm.timestamp_source(fm.canonical(raw))[1])
        if before is not None and after != before + timedelta(seconds=op.seconds):
            raise PatchError(f"record {rid}: timestamp {before} → {after}, not +{op.seconds}s")
        rep.edited.append(rid)


def _set_user(con: duckdb.DuckDBPyConnection, op: SetUser, rep: PatchReport) -> None:
    want = account_key(op.user)
    for rid in op.record_ids:
        raw = _load_raw(con, rid)
        present = [k for k in USER_KEYS if _has(raw, k)]
        for key in present + [k for k in USER_KEYS if k not in present]:
            trial = copy.deepcopy(raw)
            old = next((c[key] for c in _containers(trial) if key in c), None)
            prefix = old.rsplit("\\", 1)[0] + "\\" if isinstance(old, str) and "\\" in old else ""
            _set(trial, key, prefix + op.user)
            _, table, row = derive_record(rid, trial)
            if table and row and _acting_user({**row, "_table": table}) == want:
                _rewrite(con, rid, trial)
                rep.edited.append(rid)
                break
        else:
            raise PatchError(f"record {rid}: no raw field gives acting user {op.user}")


def _set_field(con: duckdb.DuckDBPyConnection, op: SetField, rep: PatchReport) -> None:
    for rid in op.record_ids:
        raw = _load_raw(con, rid)
        _set(raw, op.key, op.value)
        _rewrite(con, rid, raw)
        rep.edited.append(rid)


def apply_patch(con: duckdb.DuckDBPyConnection, patch: DbPatch) -> PatchReport:
    rep = PatchReport()
    for op in patch.ops:
        if isinstance(op, MoveHost):
            _move_host(con, op, rep)
        elif isinstance(op, Remove):
            _remove(con, op, rep)
        elif isinstance(op, Add):
            _add(con, op, rep)
        elif isinstance(op, TimeShift):
            _time_shift(con, op, rep)
        elif isinstance(op, SetUser):
            _set_user(con, op, rep)
        else:
            _set_field(con, op, rep)
    return rep


def patch_database(src: Path, dst: Path, patch: DbPatch) -> PatchReport:
    with patch_copy(src, dst) as con:
        return apply_patch(con, patch)


def main(argv: list[str]) -> None:
    if len(argv) != 3:
        raise SystemExit(
            "usage: python -m gbya.cases.patch <window.duckdb> <case.duckdb> <patch.json>"
        )
    src, dst, spec = (Path(a) for a in argv)
    patch = DbPatch.model_validate_json(spec.read_text())
    report = patch_database(src, dst, patch)
    print(json.dumps(report.to_json()))


if __name__ == "__main__":
    main(sys.argv[1:])
