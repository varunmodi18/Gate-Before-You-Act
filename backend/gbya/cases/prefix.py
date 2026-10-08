"""Deterministic investigation prefixes (plan §D.11 "Prefix builder", A-3, T4.3).

For each case: a profile summary, 2-4 canned ``sql_query`` calls, and a findings message. The
queries are run on the case database through the agent's own ``sql_query`` tool (execution path:
guard, rendering, provenance registration), so the prefix is consistent with the database and
its registry is exactly what an agent would have retrieved.

Queries: one per table holding cited records (raw-only records via ``raw_events``), selecting the
cited records together with up to ``DISTRACTORS`` neighbouring records of the same table; if that
gives fewer than two queries, a host summary of the target host's events is added (no record ids).
Every result must be shown in full (the builder refuses a truncated result), so the prefix bytes do
not depend on the token counter. Every cited ID must be registered, or the build fails.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gbya.cases.models import CaseFile, ScenarioFile
from gbya.context.models import TrustedContext
from gbya.data.connection import open_case_db
from gbya.data.fieldmap import TABLES
from gbya.llm.tokens import TokenCounter
from gbya.tools.registry import SqlQueryArgs, ToolEnvironment
from gbya.tools.state import EpisodeState

DISTRACTORS = 3
MAX_QUERIES = 4


class PrefixError(ValueError):
    pass


@dataclass
class Prefix:
    case_id: str
    profile: str
    queries: list[dict[str, Any]] = field(default_factory=list)
    findings: str = ""
    retrieved: list[int] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "profile": self.profile, "queries": self.queries,
                "findings": self.findings, "retrieved": self.retrieved}  # fmt: skip

    def text(self) -> str:
        return json.dumps(self.to_json(), indent=2, ensure_ascii=False) + "\n"


def _tables_of(con: Any, ids: list[int]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    ph = ", ".join("?" for _ in ids)
    for t in TABLES:
        rows = con.execute(f'SELECT record_id FROM "{t}" WHERE record_id IN ({ph})', ids).fetchall()
        if rows:
            found[t] = sorted(int(r[0]) for r in rows)
    in_tables = {i for v in found.values() for i in v}
    raw_only = [i for i in ids if i not in in_tables]
    if raw_only:
        found["raw_events"] = sorted(raw_only)
    return found


def _neighbours(con: Any, table: str, cited: list[int]) -> list[int]:
    ph = ", ".join("?" for _ in cited)
    lo, hi = min(cited), max(cited)
    rows = con.execute(
        f'SELECT record_id FROM "{table}" WHERE record_id NOT IN ({ph}) '
        f"ORDER BY abs(record_id - ?) , record_id LIMIT ?",
        [*cited, (lo + hi) // 2, DISTRACTORS],
    ).fetchall()
    return sorted(int(r[0]) for r in rows)


def _ids_sql(ids: list[int]) -> str:
    return ", ".join(str(i) for i in ids)


def build_prefix(
    case: CaseFile, sc: ScenarioFile, db_path: Path, context: TrustedContext, counter: TokenCounter
) -> Prefix:
    cited = sorted(set(case.package.cited))
    con = open_case_db(db_path)
    try:
        events = int(con.execute("SELECT count(*) FROM raw_events").fetchone()[0])  # type: ignore[index]
        prefix = Prefix(case.id, (
            f"Profile of window {sc.window_id}: {events} events. Request: {case.request.text} "
            f"Target: {json.dumps(case.request.target, sort_keys=True)}."
        ))  # fmt: skip
        sqls: list[str] = []
        for table, ids in _tables_of(con, cited).items():
            extra = _neighbours(con, table, ids) if table != "raw_events" else []
            chosen = _ids_sql(sorted(set(ids) | set(extra)))
            # bare table names: they come from the fixed table list (the guard rejects quoted ones)
            sqls.append(f"SELECT * FROM {table} WHERE record_id IN ({chosen}) ORDER BY record_id")
        if len(sqls) > MAX_QUERIES:
            raise PrefixError(
                f"{case.id}: cited records span {len(sqls)} tables (max {MAX_QUERIES})"
            )
        if len(sqls) < 2:
            target = sc.target_host.replace("'", "''")
            sqls.append(
                "SELECT host, count(*) AS events FROM process_create "
                f"WHERE host = '{target}' GROUP BY host"
            )
        env = ToolEnvironment(con=con, context=context, state=EpisodeState(), counter=counter)
        for sql in sqls:
            res = env.sql_query(SqlQueryArgs(sql=sql))
            if not res.ok:
                raise PrefixError(f"{case.id}: canned query failed: {res.content}")
            data = res.data or {}
            if "more row(s) not shown" in res.content:
                raise PrefixError(f"{case.id}: canned query result truncated: {sql}")
            prefix.queries.append({"sql": sql, "result": res.content,
                                   "rows": data.get("rows_shown"),
                                   "registered": sorted(res.registered)})  # fmt: skip
        prefix.retrieved = sorted(env.state.retrieved)
        missing = sorted(set(cited) - env.state.retrieved)
        if missing:
            raise PrefixError(f"{case.id}: cited records {missing} are not in the prefix results")
        prefix.findings = (
            f"Records {', '.join(str(i) for i in cited)} look relevant to the request; "
            "deciding on the next action."
        )
        return prefix
    finally:
        con.close()


def load_prefix(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
