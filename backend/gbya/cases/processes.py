"""Process identities in log records, for E2 sampling (team decision after M4; plan §D.11).

A record's **acting process** is its acting PID (the gate's role table, §D.6.2a) on its host,
with the Sysmon process GUID when the event carries one (``ProcessGuid``; ``SourceProcessGUID``
for process access). Other processes named in a record are its parent (``ppid`` /
``ParentProcessGuid``) and, for process access, its target (``target_pid`` /
``TargetProcessGUID``).

Two identities are the same process when host and PID agree and, if both carry a GUID, the GUIDs
agree. Without GUIDs this errs towards "same", which only removes more candidates from E2's pool.

``attack_related_records`` returns every record whose acting process is a process of a cited
record, or a parent or child of one: such records are never sampled as E2's "unrelated" evidence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import duckdb

from gbya.context.models import host_key
from gbya.data.fieldmap import TABLES
from gbya.gate.checks import ACTOR_PID
from gbya.gate.evidence import CitedRecord, read_cited

ACTOR_GUID = {"process_access": ("SourceProcessGUID", "SourceProcessGuid")}
DEFAULT_GUID = ("ProcessGuid",)


@dataclass(frozen=True)
class Proc:
    host: str
    pid: int
    guid: str | None = None

    def same(self, other: Proc) -> bool:
        if self.host != other.host or self.pid != other.pid:
            return False
        return self.guid is None or other.guid is None or self.guid == other.guid


def _guid(raw: dict[str, Any], keys: Iterable[str]) -> str | None:
    containers = [raw, raw.get("event_data")] if isinstance(raw.get("event_data"), dict) else [raw]
    for c in containers:
        for k in keys:
            v = c.get(k) if isinstance(c, dict) else None
            if v:
                return str(v).strip("{}").lower()
    return None


def acting_process(rec: CitedRecord) -> Proc | None:
    if rec.table is None or rec.host is None:
        return None
    pid = next((rec.values.get(c) for c in ACTOR_PID.get(rec.table, ()) if rec.values.get(c)), None)
    if pid is None:
        return None
    guid = _guid(rec.raw, ACTOR_GUID.get(rec.table, DEFAULT_GUID))
    return Proc(host_key(rec.host), int(pid), guid)


def parent_process(rec: CitedRecord) -> Proc | None:
    if rec.table is None or rec.host is None or not rec.values.get("ppid"):
        return None
    return Proc(host_key(rec.host), int(rec.values["ppid"]), _guid(rec.raw, ("ParentProcessGuid",)))


def named_processes(rec: CitedRecord) -> list[Proc]:
    """Every process a record names: actor, parent and (process access) target."""
    out = []
    if (actor := acting_process(rec)) is not None:
        out.append(actor)
    if rec.table is None or rec.host is None:
        return out
    host = host_key(rec.host)
    if (parent := parent_process(rec)) is not None:
        out.append(parent)
    if rec.values.get("target_pid"):
        guid = _guid(rec.raw, ("TargetProcessGUID", "TargetProcessGuid"))
        out.append(Proc(host, int(rec.values["target_pid"]), guid))
    return out


def _creates(con: duckdb.DuckDBPyConnection) -> list[CitedRecord]:
    ids = [int(r[0]) for r in con.execute("SELECT record_id FROM process_create").fetchall()]
    recs = read_cited(con, ids)
    return [recs[i] for i in ids if i in recs]


def attack_related_records(con: duckdb.DuckDBPyConnection, cited: Iterable[int]) -> set[int]:
    """Records whose acting process is a cited record's process, or its parent or child."""
    seeds = [p for r in read_cited(con, list(cited)).values() for p in named_processes(r)]
    procs = list(seeds)
    for c in _creates(con):  # one generation of lineage around every seed process
        child, parent = acting_process(c), parent_process(c)
        if child is None:
            continue
        if any(child.same(s) for s in seeds) and parent is not None:
            procs.append(parent)  # parent of a seed
        if parent is not None and any(parent.same(s) for s in seeds):
            procs.append(child)  # child of a seed
    ids = [
        int(r[0]) for t in TABLES for r in con.execute(f'SELECT record_id FROM "{t}"').fetchall()
    ]
    related = set()
    for rec in read_cited(con, ids).values():
        actor = acting_process(rec)
        if actor is not None and any(actor.same(p) for p in procs):
            related.add(rec.record_id)
    return related


def describe(rec: CitedRecord) -> dict[str, Any]:
    """A compact view of a record for the Annotate page's E2 list."""
    actor = acting_process(rec)
    keys = ("image", "source_image", "command_line", "target_image", "target_object",
            "target_filename", "dst_ip")  # fmt: skip
    return {
        "record_id": rec.record_id, "table": rec.table, "host": rec.host,
        "ts": rec.ts.isoformat() if rec.ts else None,
        "pid": actor.pid if actor else None, "guid": actor.guid if actor else None,
        "summary": json.dumps({k: rec.values[k] for k in keys if rec.values.get(k)})[:300],
    }  # fmt: skip
