"""SQL guard, layer 1 (plan §D.5.1, FR-09, T1.3a): an AST whitelist in front of DuckDB.

Accepted: exactly one ``SELECT`` statement (or a ``UNION`` of selects) that reads only whitelisted
tables by plain name, contains no write or DDL anywhere in its tree (including inside CTEs), and
calls no denylisted function. The accepted query is regenerated from the AST (so comments cannot
swallow the wrapper) and wrapped as ``SELECT * FROM (<sql>) LIMIT 50``.

Layer 2 — the hardened read-only connection — is ``gbya.data.connection.open_case_db``; it holds
even when this layer is bypassed. ``run_query`` executes a guarded query with a 2 s watchdog.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from gbya.errors import GbyaError

WHITELIST: frozenset[str] = frozenset(
    {
        "process_create",
        "process_access",
        "network",
        "registry",
        "file",
        "logon",
        "share_access",
        "raw_events",
    }
)
ROW_LIMIT = 50
TIMEOUT_S = 2.0

# Plan §D.5.1 denylist, plus other DuckDB functions that read files, run SQL text or expose the
# engine's configuration. Matching is on the lower-cased function name.
DENY_PREFIXES = ("read_", "parquet_", "duckdb_", "pragma_")
DENY_NAMES = frozenset(
    {
        "copy",
        "attach",
        "install",
        "load",
        "pragma",
        "system",
        "getenv",
        "glob",
        "query",
        "query_table",
        "sniff_csv",
        "current_setting",
        "getvariable",
        "set_variable",
        "iceberg_scan",
        "delta_scan",
    }
)
# Nodes that write, change the schema or the session, or are not plain queries.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Copy,
    exp.Command,
    exp.Pragma,
    exp.Attach,
    exp.Detach,
    exp.Install,
    exp.Set,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Describe,
    exp.Into,
    exp.TruncateTable,
)


class SqlRejected(GbyaError):
    code = "SQL_REJECTED"
    http_status = 400


class SqlTimeout(GbyaError):
    code = "SQL_TIMEOUT"
    http_status = 400


@dataclass(frozen=True)
class GuardedQuery:
    original: str
    inner_sql: str  # regenerated from the AST
    wrapped_sql: str  # SELECT * FROM (<inner_sql>) LIMIT 50
    tree: exp.Expression  # parsed inner statement (used by the provenance registry, T2.3)


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return str(func.name).lower()
    return func.sql_name().lower()


def _reject(message: str, hint: str | None = None, **details: Any) -> SqlRejected:
    return SqlRejected(message, hint=hint, details=details)


def guard(sql: str) -> GuardedQuery:
    """Validate ``sql``; raise ``SqlRejected`` with a reason, or return the wrapped query."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as exc:
        raise _reject("The SQL could not be parsed", error=str(exc).splitlines()[0]) from exc
    if len(statements) != 1:
        raise _reject(
            "Exactly one statement is allowed",
            hint="Remove the extra statements",
            statements=len(statements),
        )
    stmt = statements[0]

    if not isinstance(stmt, exp.Select | exp.Union):
        raise _reject(
            "Only SELECT statements are allowed",
            hint="Use a single SELECT (UNION of SELECTs is allowed)",
            statement=type(stmt).__name__,
        )
    for node in stmt.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise _reject(
                "Only read-only SELECT queries are allowed",
                hint=f"Remove the {type(node).__name__.upper()} clause",
                node=type(node).__name__,
            )
        if isinstance(node, exp.Union | exp.Select | exp.Subquery | exp.CTE):
            continue
        if isinstance(node, exp.SetOperation):
            raise _reject("Only UNION is allowed between SELECTs", node=type(node).__name__)

    cte_names = {str(cte.alias_or_name).lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise _reject(
                "Table functions are not allowed",
                hint="Query the log tables by name",
                source=table.this.sql(dialect="duckdb")[:80],
            )
        if table.args.get("db") or table.args.get("catalog"):
            raise _reject(
                "Qualified table names are not allowed",
                hint="Use the plain table name",
                table=table.sql(dialect="duckdb"),
            )
        name = str(table.name).lower()
        if name in cte_names and not table.this.quoted:
            continue
        if table.this.quoted or name not in WHITELIST:
            raise _reject(
                f"Unknown table: {table.name}",
                hint="Allowed tables: " + ", ".join(sorted(WHITELIST)),
                table=table.name,
            )

    for func in stmt.find_all(exp.Func):
        name = _function_name(func)
        if name.startswith(DENY_PREFIXES) or name in DENY_NAMES:
            raise _reject(f"Function not allowed: {name}", function=name)

    inner = stmt.sql(dialect="duckdb")
    wrapped = f"SELECT * FROM ({inner}) LIMIT {ROW_LIMIT}"
    return GuardedQuery(original=sql, inner_sql=inner, wrapped_sql=wrapped, tree=stmt)


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]


def execute_with_timeout(
    con: duckdb.DuckDBPyConnection, sql: str, timeout_s: float = TIMEOUT_S
) -> QueryResult:
    """Run ``sql`` on ``con``; a watchdog interrupts it after ``timeout_s`` seconds."""
    done = threading.Event()
    timed_out = threading.Event()

    def watchdog() -> None:
        if not done.wait(timeout_s):
            timed_out.set()
            con.interrupt()

    t = threading.Thread(target=watchdog, daemon=True)
    t.start()
    try:
        cur = con.execute(sql)
        rows = cur.fetchall()
        columns = [d[0] for d in (cur.description or [])]
    except duckdb.InterruptException as exc:
        raise SqlTimeout(f"Query exceeded {timeout_s:g} s", hint="Narrow the query") from exc
    except duckdb.Error as exc:
        if timed_out.is_set():
            raise SqlTimeout(f"Query exceeded {timeout_s:g} s", hint="Narrow the query") from exc
        raise SqlRejected("The query failed", details={"error": str(exc)[:300]}) from exc
    finally:
        done.set()
        t.join()
    return QueryResult(columns=columns, rows=rows)


def run_query(
    con: duckdb.DuckDBPyConnection, sql: str, timeout_s: float = TIMEOUT_S
) -> tuple[GuardedQuery, QueryResult]:
    """Guard ``sql`` (layer 1) and run it on a hardened connection (layer 2)."""
    q = guard(sql)
    return q, execute_with_timeout(con, q.wrapped_sql, timeout_s)
