"""Execution path for log databases (plan §D.1.1, §D.5.1 layer 2, T1.3a).

``open_case_db`` is the ONLY way agent-facing and inspection code (tools, gate, agent, retrieval,
API, worker, scoring, prefix builder) opens a log database. The connection is read-only, then
external access and extension auto-install/auto-load are disabled, resources are capped and the
configuration is locked, in that order. These are database-level controls, not an OS sandbox;
they were verified on the pinned DuckDB version (tests repeat the check).
"""

from __future__ import annotations

from pathlib import Path

import duckdb

HARDENING: tuple[str, ...] = (
    "SET enable_external_access = false",
    "SET autoinstall_known_extensions = false",
    "SET autoload_known_extensions = false",
    "SET memory_limit = '1GB'",
    "SET threads = 2",
    "SET lock_configuration = true",
)


class CaseDbNotFound(FileNotFoundError):
    pass


def open_case_db(path: Path) -> duckdb.DuckDBPyConnection:
    """Open a window or case database read-only, hardened and locked."""
    if not path.is_file():
        raise CaseDbNotFound(str(path))
    con = duckdb.connect(str(path), read_only=True)
    try:
        for statement in HARDENING:
            con.execute(statement)
    except BaseException:
        con.close()
        raise
    return con
