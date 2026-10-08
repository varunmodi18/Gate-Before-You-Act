"""Execution path for log databases (plan §D.1.1, §D.5.1 layer 2, T1.3a).

``open_case_db`` is the ONLY way agent-facing and inspection code (tools, gate, agent, retrieval,
API, worker, scoring, prefix builder) opens a log database. The connection is read-only, with
external access and extension auto-install/auto-load disabled, resources capped and the
configuration locked. These are database-level controls, not an OS sandbox; they were verified
on the pinned DuckDB version (tests repeat the check).

The settings are passed as connection-time configuration rather than as ``SET`` statements after
opening (plan §D.5.1 lists them as ``SET``s). DuckDB shares one database instance per file within a
process, so a second ``open_case_db`` of the same file would otherwise hit the already-locked
configuration and fail. With connection-time configuration every hardened connection to a file
shares the same locked instance, and an unhardened connection to that file in the same process is
refused by DuckDB.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import duckdb

# Same settings and order as plan §D.5.1; the configuration is locked last.
HARDENING: MappingProxyType[str, str | int | float] = MappingProxyType(
    {
        "enable_external_access": False,
        "autoinstall_known_extensions": False,
        "autoload_known_extensions": False,
        "memory_limit": "1GB",
        "threads": 2,
        "lock_configuration": True,
    }
)


class CaseDbNotFound(FileNotFoundError):
    pass


def open_case_db(path: Path) -> duckdb.DuckDBPyConnection:
    """Open a window or case database read-only, hardened and locked."""
    if not path.is_file():
        raise CaseDbNotFound(str(path))
    return duckdb.connect(str(path), read_only=True, config=dict(HARDENING))
