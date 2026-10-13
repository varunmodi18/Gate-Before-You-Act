"""Process identities in log records, for E2 sampling (team decision after M4; plan §D.11).

A record's **acting process** is its acting PID (the gate's role table, §D.6.2a) on its host,
with the Sysmon process GUID when the event carries one (``ProcessGuid``; ``SourceProcessGUID``
for process access). A raw-only record (an event type that is not normalised, e.g. Sysmon 7 image
loads) takes its acting PID from the raw event's ``ProcessId``. Other processes named in a record
are its parent (``ppid`` / ``ParentProcessGuid``) and, for process access, its target
(``target_pid`` / ``TargetProcessGUID``).

Two identities are the same process when host and PID agree and, if both carry a GUID, the GUIDs
agree. Without GUIDs this errs towards "same", which only removes more candidates from E2's pool.

**Attack process set** (team decision at R1, 13 Oct 2026): the acting processes of the scenario's
cited evidence records, plus their recorded parents and children. Targets of cited records (such
as ``lsass.exe``) are **not** added, so an unrelated process's benign access to lsass stays
eligible. ``attack_related_records`` returns every record whose acting process **or** target
process is in the set: such records are never sampled as E2's "unrelated" evidence (e.g. a
process opening the attacking process is excluded).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import duckdb

from gbya.context.models import host_key
from gbya.gate.checks import ACTOR_PID
from gbya.gate.evidence import CitedRecord, read_cited

ACTOR_GUID = {"process_access": ("SourceProcessGUID", "SourceProcessGuid")}
DEFAULT_GUID = ("ProcessGuid",)
RAW_PID = ("ProcessId", "ProcessID")


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


def _raw_pid(raw: dict[str, Any]) -> int | None:
    containers = [raw, raw.get("event_data")] if isinstance(raw.get("event_data"), dict) else [raw]
    for c in containers:
        for k in RAW_PID:
            v = c.get(k) if isinstance(c, dict) else None
            if v in (None, ""):
                continue
            try:
                return int(str(v), 0) if str(v).lower().startswith("0x") else int(str(v))
            except ValueError:
                return None
    return None


def acting_process(rec: CitedRecord) -> Proc | None:
    if rec.host is None:
        return None
    if rec.table is None:  # raw-only record: the raw event's own process
        raw_pid = _raw_pid(rec.raw)
        if raw_pid is None:
            return None
        return Proc(host_key(rec.host), raw_pid, _guid(rec.raw, DEFAULT_GUID))
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


def target_process(rec: CitedRecord) -> Proc | None:
    """The accessed process of a process-access record (None for other records)."""
    if rec.table is None or rec.host is None or not rec.values.get("target_pid"):
        return None
    guid = _guid(rec.raw, ("TargetProcessGUID", "TargetProcessGuid"))
    return Proc(host_key(rec.host), int(rec.values["target_pid"]), guid)


def attack_processes(con: duckdb.DuckDBPyConnection, cited: Iterable[int]) -> list[Proc]:
    """The acting processes of the cited records, plus their recorded parents and children."""
    recs = list(read_cited(con, list(cited)).values())
    seeds = [a for r in recs if (a := acting_process(r)) is not None]
    procs = list(seeds)
    procs += [
        pp for r in recs if (pp := parent_process(r)) is not None
    ]  # a cited creation's parent
    for c in _creates(con):  # one generation of recorded lineage around every seed process
        child, parent = acting_process(c), parent_process(c)
        if child is None or parent is None:
            continue
        if any(child.same(s) for s in seeds):
            procs.append(parent)  # parent of a seed
        if any(parent.same(s) for s in seeds):
            procs.append(child)  # child of a seed
    return procs


def attack_related_records(con: duckdb.DuckDBPyConnection, cited: Iterable[int]) -> set[int]:
    """Records whose acting process or target process is in the attack process set."""
    procs = attack_processes(con, cited)
    # every record, raw-only ones included (their acting PID comes from the raw event)
    ids = [int(r[0]) for r in con.execute("SELECT record_id FROM raw_events").fetchall()]
    related = set()
    for rec in read_cited(con, ids).values():
        for proc in (acting_process(rec), target_process(rec)):
            if proc is not None and any(proc.same(p) for p in procs):
                related.add(rec.record_id)
                break
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
