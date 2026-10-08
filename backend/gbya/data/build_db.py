"""Construction path for log databases (plan §D.1.1). WRITABLE — build steps only.

Allowed callers: ``gbya.data.normalise``, ``gbya.cases.patch`` and the ``make data`` /
``make import-cases`` command-line entry points. The API, worker and every agent-facing module
use the hardened read-only ``gbya.data.connection.open_case_db`` instead and must never import
this module (an import-graph test enforces this in T1.3a).

A database is built into a temporary file, closed, made read-only on disk (mode 0444) and then
moved into place, so a half-built file is never visible under the final name.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb

READ_ONLY_MODE = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH  # 0o444


def open_for_build(path: Path) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) a writable DuckDB file. Construction path only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


@contextmanager
def build_database(final_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Build a new database at ``final_path`` atomically; the result is mode 0444."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_path.with_name(final_path.name + ".building")
    for leftover in (tmp, tmp.with_name(tmp.name + ".wal")):
        if leftover.exists():
            leftover.chmod(stat.S_IRUSR | stat.S_IWUSR)
            leftover.unlink()
    con = open_for_build(tmp)
    try:
        yield con
        con.execute("CHECKPOINT")
    except BaseException:
        con.close()
        tmp.unlink(missing_ok=True)
        raise
    con.close()
    tmp.chmod(READ_ONLY_MODE)
    if final_path.exists():
        final_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, final_path)
