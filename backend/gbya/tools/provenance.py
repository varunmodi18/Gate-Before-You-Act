"""Retrieved-record registry (plan §D.5.2, FR-10, T2.3).

The claim this supports: *an identifier used in a tool argument really occurs in a log record the
agent has retrieved.* A record id is registered only when it reached the model through a **direct
reference to ``record_id`` of a whitelisted base table** in the query's syntax tree; the result
values themselves are never trusted. Rules (§D.5.2 table):

==============================================================  ===========
Output column                                                   Registered?
==============================================================  ===========
direct base-table ``record_id`` (aliased or not)                yes, for each row actually shown
literal, expression or function result                          no
any column of a query with GROUP BY, an aggregate or DISTINCT   no
column resolving to a CTE or derived table                      no
UNION                                                           only if direct in every branch
joins                                                           each direct ``record_id`` column
==============================================================  ===========

Each candidate id is then confirmed to exist in ``raw_events``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import duckdb
from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify

from gbya.data.fieldmap import COMMON_COLUMNS, TABLE_COLUMNS
from gbya.tools.sql_guard import WHITELIST

SCHEMA: dict[str, dict[str, str]] = {
    t: {c: ty.split()[0] for c, ty in COMMON_COLUMNS + cols} for t, cols in TABLE_COLUMNS.items()
}
SCHEMA["raw_events"] = {
    "record_id": "BIGINT",
    "channel": "VARCHAR",
    "event_id": "INTEGER",
    "json": "VARCHAR",
}


def _sources(select: exp.Select) -> dict[str, exp.Expression]:
    """alias → source (Table, Subquery, …) for the FROM clause and joins of one SELECT."""
    out: dict[str, exp.Expression] = {}
    frm = select.args.get("from") or select.args.get("from_")
    items: list[exp.Expression] = [frm.this] if isinstance(frm, exp.From) else []
    items += [j.this for j in select.args.get("joins") or []]
    for src in items:
        out[str(src.alias_or_name).lower()] = src
    return out


def _has_aggregate(projection: exp.Expression) -> bool:
    """An aggregate that collapses rows (window aggregates do not)."""
    return any(a.find_ancestor(exp.Window) is None for a in projection.find_all(exp.AggFunc))


def _select_positions(select: exp.Select, cte_names: set[str]) -> list[bool]:
    projections = list(select.expressions)
    if (
        select.args.get("group")
        or select.args.get("distinct")
        or select.args.get("having")
        or any(_has_aggregate(p) for p in projections)
    ):
        return [False] * len(projections)
    sources = _sources(select)
    out = []
    for p in projections:
        col = p.this if isinstance(p, exp.Alias) else p
        direct = False
        if isinstance(col, exp.Column) and str(col.name).lower() == "record_id":
            src = sources.get(str(col.table).lower()) if col.table else None
            if src is None and not col.table and len(sources) == 1:
                src = next(iter(sources.values()))
            direct = (
                isinstance(src, exp.Table)
                and isinstance(src.this, exp.Identifier)
                and str(src.name).lower() in WHITELIST
                and str(src.name).lower() not in cte_names
            )
        out.append(direct)
    return out


def _positions(node: Any, cte_names: set[str]) -> list[bool] | None:
    if isinstance(node, exp.Union):
        left = _positions(node.left, cte_names)
        right = _positions(node.right, cte_names)
        if left is None or right is None or len(left) != len(right):
            return None
        return [a and b for a, b in zip(left, right, strict=True)]
    if isinstance(node, exp.Select):
        return _select_positions(node, cte_names)
    return None  # anything else (e.g. INTERSECT): register nothing


def direct_record_id_positions(tree: exp.Expression) -> list[bool] | None:
    """Per output column, whether it is a direct base-table ``record_id``; None if unsure."""
    cte_names = {str(c.alias_or_name).lower() for c in tree.find_all(exp.CTE)}
    try:
        qualified = qualify(
            tree.copy(),
            schema=cast(dict[str, object], SCHEMA),
            dialect="duckdb",
            validate_qualify_columns=False,
            quote_identifiers=False,
        )
    except (OptimizeError, SqlglotError):
        return None
    return _positions(qualified, cte_names)


def candidate_ids(
    positions: Sequence[bool] | None, columns: Sequence[str], rows_shown: Sequence[Sequence[Any]]
) -> set[int]:
    """Integer values at direct positions, from the rows actually shown to the model."""
    if positions is None or len(positions) != len(columns):
        return set()  # shape mismatch: register nothing (fail safe)
    ids: set[int] = set()
    for row in rows_shown:
        for i, direct in enumerate(positions):
            v = row[i]
            if direct and isinstance(v, int) and not isinstance(v, bool):
                ids.add(v)
    return ids


def confirm_in_raw_events(con: duckdb.DuckDBPyConnection, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    placeholders = ", ".join("?" for _ in ids)
    rows = con.execute(
        f"SELECT record_id FROM raw_events WHERE record_id IN ({placeholders})", sorted(ids)
    ).fetchall()
    return {int(r[0]) for r in rows}


def register(
    con: duckdb.DuckDBPyConnection,
    tree: exp.Expression,
    columns: Sequence[str],
    rows_shown: Sequence[Sequence[Any]],
) -> set[int]:
    """Record ids to add to ``EpisodeState.retrieved`` for one query result."""
    candidates = candidate_ids(direct_record_id_positions(tree), columns, rows_shown)
    return confirm_in_raw_events(con, candidates)
