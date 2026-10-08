"""Cited records re-read from the canonical database, and their deterministic rendering
(plan §D.5.2 "Cited evidence", §D.7.1).

C2, C3 and C4 never use values copied from query results: they re-read each cited record by
``record_id`` here. The rendering gives the verifier **selected normalised fields without
truncation**: per table, the fields of §D.7.1 in fixed order, each value exactly as stored (JSON
string escaping only). ``hashes``, ``call_trace`` and the raw event blob are omitted by design.
A package over the budget (8 records, 3,200 tokens) is rejected, never trimmed. T3.2 adds the
rendering manifest and the verifier prompt on top of this module.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import duckdb

from gbya.data.fieldmap import TABLES
from gbya.llm.tokens import TokenCounter

MAX_CITED = 8
MAX_EVIDENCE_TOKENS = 3200
OPEN, CLOSE = "<<UNTRUSTED_LOG_DATA>>", "<</UNTRUSTED_LOG_DATA>>"

COMMON_FIELDS = ("record_id", "ts", "host", "channel", "event_id")
DECISION_FIELDS: dict[str, tuple[str, ...]] = {
    "process_create": ("image", "command_line", "parent_image", "parent_command_line", "pid",
                       "ppid", "user", "integrity_level"),
    "process_access": ("source_image", "source_pid", "target_image", "target_pid",
                       "granted_access", "user"),
    "network": ("image", "pid", "src_ip", "src_port", "dst_ip", "dst_port", "protocol",
                "direction", "user"),
    "registry": ("event_type", "image", "pid", "target_object", "details", "user"),
    "file": ("image", "pid", "target_filename", "event_type", "user"),
    "logon": ("subject_user", "target_user", "logon_type", "src_ip", "workstation",
              "process_name"),
    "share_access": ("subject_user", "share_name", "relative_target", "src_ip", "access_mask"),
}  # fmt: skip
OMITTED_FIELDS = ("hashes", "call_trace", "raw_json")


@dataclass(frozen=True)
class CitedRecord:
    """One cited record as stored. ``table`` is None for events kept only in raw_events."""

    record_id: int
    table: str | None
    values: dict[str, Any] = field(default_factory=dict)  # every column of its table
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def host(self) -> str | None:
        h = self.values.get("host")
        if h is None:
            raw_host = self.raw.get("Hostname") or self.raw.get("Computer")
            raw_host = raw_host or self.raw.get("computer_name")
            h = str(raw_host) if raw_host else None
        return h

    @property
    def ts(self) -> datetime | None:
        v = self.values.get("ts")
        return v if isinstance(v, datetime) else None


def read_cited(con: duckdb.DuckDBPyConnection, ids: Iterable[int]) -> dict[int, CitedRecord]:
    """Re-read cited records from the canonical database. Unknown ids are simply absent."""
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return {}
    ph = ", ".join("?" for _ in wanted)
    raws = {
        int(r[0]): json.loads(r[1])
        for r in con.execute(
            f"SELECT record_id, json FROM raw_events WHERE record_id IN ({ph})", wanted
        ).fetchall()
    }
    out = {rid: CitedRecord(rid, None, {}, raw) for rid, raw in raws.items()}
    for table in TABLES:
        cur = con.execute(f'SELECT * FROM "{table}" WHERE record_id IN ({ph})', wanted)
        cols = [d[0] for d in cur.description or []]
        for row in cur.fetchall():
            values = dict(zip(cols, row, strict=True))
            rid = int(values["record_id"])
            out[rid] = CitedRecord(rid, table, values, raws.get(rid, {}))
    return out


def window_time_range(con: duckdb.DuckDBPyConnection) -> tuple[datetime, datetime] | None:
    parts = " UNION ALL ".join(f'SELECT ts FROM "{t}"' for t in TABLES)
    row = con.execute(f"SELECT min(ts), max(ts) FROM ({parts})").fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


def _json_value(v: Any) -> Any:
    return v.isoformat() if isinstance(v, datetime) else v


def render_record(rec: CitedRecord) -> tuple[str, dict[str, Any]]:
    """One record → (JSON line, structured {field: value}) with the §D.7.1 projection."""
    if rec.table is None:
        fields: dict[str, Any] = {
            "record_id": rec.record_id,
            "host": rec.host,
            "channel": rec.raw.get("Channel") or rec.raw.get("log_name"),
            "event_id": rec.raw.get("EventID", rec.raw.get("event_id")),
            "note": "not normalised; raw event omitted by design",
        }
    else:
        fields = {"table": rec.table}
        for name in COMMON_FIELDS + DECISION_FIELDS[rec.table]:
            fields[name] = _json_value(rec.values.get(name))
    return json.dumps(fields, ensure_ascii=False), fields


@dataclass(frozen=True)
class RenderedEvidence:
    text: str  # the CITED_RECORDS block, wrapped as untrusted
    structured: dict[int, dict[str, Any]]  # record → field → rendered value
    tokens: int
    records: int


def render_cited(records: list[CitedRecord], counter: TokenCounter) -> RenderedEvidence:
    lines, structured = [], {}
    for rec in records:
        line, fields = render_record(rec)
        lines.append(line)
        structured[rec.record_id] = fields
    text = OPEN + "\n" + "".join(f"{line}\n" for line in lines) + CLOSE
    return RenderedEvidence(text, structured, counter.count(text), len(records))
