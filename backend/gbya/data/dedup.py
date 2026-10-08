"""Eligibility and de-duplication of windows (plan §D.2 steps 1-3, T1.5).

1. **Eligibility:** the window has ≥ 30 events and its *primary host* itself has ≥ 1 event in
   ``process_create`` or ``process_access`` (events on other hosts do not count). The primary host
   is the host with the most ``process_create`` + ``process_access`` events (ties: more events
   overall, then name). Its share of all the window's events is reported so multi-host windows are
   visible (team decision, 8 Oct 2026).
2. **Signatures:** per window, the *set* of tuples
   ``(event_id, image, normalised command line, parent image, target)`` over the mapped tables,
   lower-cased, with GUIDs, hex addresses, digit runs longer than 4 and temp-path components
   replaced by placeholders (see ``SIGNATURE_FIELDS`` for what "image" and "target" are per table).
3. **Grouping:** pairwise Jaccard similarity of the signature sets; pairs with J > 0.5 are joined
   with union-find. A group's id is its smallest window id, so grouping is deterministic.

    python -m gbya.data.dedup          # make dedup
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import duckdb

from gbya.data.connection import open_case_db

JACCARD_THRESHOLD = 0.5
MIN_EVENTS = 30

Signature = tuple[int | None, str, str, str, str]

# Per table: SQL expressions for (image, command line, parent image, target).
SIGNATURE_FIELDS: dict[str, tuple[str, str, str, str]] = {
    "process_create": ("image", "command_line", "parent_image", "NULL"),
    "process_access": ("source_image", "NULL", "NULL", "target_image"),
    "network": ("image", "NULL", "NULL", "dst_ip || ':' || CAST(dst_port AS VARCHAR)"),
    "registry": ("image", "NULL", "NULL", "target_object"),
    "file": ("image", "NULL", "NULL", "target_filename"),
    "logon": ("process_name", "NULL", "NULL", "target_user"),
    "share_access": ("NULL", "NULL", "NULL", "share_name || '\\' || coalesce(relative_target, '')"),
}

_GUID = re.compile(r"\{?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}?", re.I)
_HEX = re.compile(r"\b0x[0-9a-f]+\b", re.I)
_LONG_DIGITS = re.compile(r"\d{5,}")
# A path component directly under a Temp directory (random file or folder names).
_TEMP = re.compile(r"(\\(?:appdata\\local\\)?temp\\)[^\\\s\"']+", re.I)


def normalise_text(value: str | None) -> str:
    """Lower-case and replace volatile parts (§D.2 step 2)."""
    if not value:
        return ""
    s = value.lower()
    s = _GUID.sub("<guid>", s)
    s = _HEX.sub("<hex>", s)
    s = _TEMP.sub(r"\1<tmp>", s)
    s = _LONG_DIGITS.sub("<n>", s)
    return s.strip()


def signatures(con: duckdb.DuckDBPyConnection) -> set[Signature]:
    out: set[Signature] = set()
    for table, (image, cmd, parent, target) in SIGNATURE_FIELDS.items():
        rows = con.execute(
            f'SELECT DISTINCT event_id, {image}, {cmd}, {parent}, {target} FROM "{table}"'
        ).fetchall()
        for eid, img, cl, par, tgt in rows:
            out.add(
                (
                    eid,
                    normalise_text(img),
                    normalise_text(cl),
                    normalise_text(par),
                    normalise_text(tgt),
                )
            )
    return out


def jaccard(a: set[Signature], b: set[Signature]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


class UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # keep the smaller id as root, so group ids are deterministic
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    jaccard: float


def group_windows(
    sigs: dict[str, set[Signature]], threshold: float = JACCARD_THRESHOLD
) -> tuple[dict[str, str], list[Pair]]:
    """Group id (smallest member id) per window, and every pair with J > threshold."""
    ids = sorted(sigs)
    uf = UnionFind(ids)
    linked: list[Pair] = []
    for a, b in combinations(ids, 2):
        j = jaccard(sigs[a], sigs[b])
        if j > threshold:
            uf.union(a, b)
            linked.append(Pair(a, b, j))
    return {i: uf.find(i) for i in ids}, linked


@dataclass(frozen=True)
class Eligibility:
    window_id: str
    eligible: bool
    events: int
    primary_host: str | None
    primary_host_process_events: int
    hosts: int
    reason: str
    primary_host_events: int = 0  # all events (raw_events) whose host is the primary host
    primary_host_share: float = 0.0  # primary_host_events / events


def eligibility(window_id: str, con: duckdb.DuckDBPyConnection) -> Eligibility:
    events = int(con.execute("SELECT count(*) FROM raw_events").fetchone()[0])  # type: ignore[index]
    per_host = con.execute(
        """
        WITH proc AS (
            SELECT host FROM process_create UNION ALL SELECT host FROM process_access
        ), allh AS (
            SELECT host FROM process_create UNION ALL SELECT host FROM process_access
            UNION ALL SELECT host FROM network UNION ALL SELECT host FROM registry
            UNION ALL SELECT host FROM file UNION ALL SELECT host FROM logon
            UNION ALL SELECT host FROM share_access
        )
        SELECT h.host,
               (SELECT count(*) FROM proc p WHERE p.host = h.host) AS proc_n,
               count(*) AS all_n
        FROM allh h WHERE h.host IS NOT NULL
        GROUP BY h.host
        ORDER BY proc_n DESC, all_n DESC, h.host
        """
    ).fetchall()
    primary, proc_n = (per_host[0][0], int(per_host[0][1])) if per_host else (None, 0)
    primary_events = 0
    if primary is not None:
        # Every event, mapped or not: the host as written in the raw JSON (all four formats).
        row = con.execute(
            """
            SELECT count(*) FROM raw_events
            WHERE coalesce(json_extract_string(json, '$.Hostname'),
                           json_extract_string(json, '$.Computer'),
                           json_extract_string(json, '$.computer_name')) = ?
            """,
            [primary],
        ).fetchone()
        primary_events = int(row[0]) if row else 0
    reasons = []
    if events < MIN_EVENTS:
        reasons.append(f"only {events} events (< {MIN_EVENTS})")
    if proc_n < 1:
        reasons.append("no process_create/process_access event on the primary host")
    return Eligibility(
        window_id=window_id,
        eligible=not reasons,
        events=events,
        primary_host=primary,
        primary_host_process_events=proc_n,
        hosts=len(per_host),
        reason="; ".join(reasons) or "eligible",
        primary_host_events=primary_events,
        primary_host_share=round(primary_events / events, 4) if events else 0.0,
    )


@dataclass(frozen=True)
class DedupResult:
    groups: dict[str, str]
    pairs: list[Pair]
    eligibility: dict[str, Eligibility]


def analyse(db_paths: dict[str, Path]) -> DedupResult:
    sigs: dict[str, set[Signature]] = {}
    elig: dict[str, Eligibility] = {}
    for wid, path in sorted(db_paths.items()):
        con = open_case_db(path)
        try:
            sigs[wid] = signatures(con)
            elig[wid] = eligibility(wid, con)
        finally:
            con.close()
    groups, pairs = group_windows(sigs)
    return DedupResult(groups=groups, pairs=pairs, eligibility=elig)


def main() -> None:
    import json
    from collections import Counter

    from sqlalchemy import select

    from gbya.config import get_settings
    from gbya.logging import configure_logging, get_logger
    from gbya.store.db import make_engine, make_sessionmaker, session_scope, upgrade
    from gbya.store.models import Window

    settings = get_settings()
    configure_logging("data", settings.resolve(settings.log_dir), dev=settings.env == "dev")
    log = get_logger("gbya.data.dedup")
    upgrade()
    factory = make_sessionmaker(make_engine())
    with session_scope(factory) as s:
        paths = {
            w.id: settings.resolve(Path(w.duckdb_path))
            for w in s.scalars(select(Window)).all()
            if w.duckdb_path
        }
    result = analyse(paths)
    with session_scope(factory) as s:
        for wid, gid in result.groups.items():
            row = s.get(Window, wid)
            if row is not None:
                row.dedup_group = gid
    sizes = Counter(result.groups.values())
    multi = {g: n for g, n in sizes.items() if n > 1}
    eligible = [e for e in result.eligibility.values() if e.eligible]
    report = {
        "threshold": JACCARD_THRESHOLD,
        "windows": len(result.groups),
        "groups": len(sizes),
        "multi_window_groups": multi,
        "pairs_above_threshold": [p.__dict__ for p in result.pairs],
        "eligible": len(eligible),
        "eligible_single_host": sum(1 for e in eligible if e.hosts == 1),
        "ineligible": {
            e.window_id: e.reason for e in result.eligibility.values() if not e.eligible
        },
    }
    out = settings.resolve(settings.data_dir) / "dedup_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("dedup_done", **{k: v for k, v in report.items() if k != "pairs_above_threshold"})
    print(json.dumps({k: v for k, v in report.items() if k != "pairs_above_threshold"}, indent=2))
    print(f"report: {out}")


if __name__ == "__main__":
    main()
