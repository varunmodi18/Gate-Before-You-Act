"""T1.3a: SQL guard (layer 1), hardened connection (layer 2), timeout, database boundary."""

from __future__ import annotations

import importlib
import pkgutil
import re
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from gbya.data.build_db import build_database
from gbya.data.connection import HARDENING, CaseDbNotFound, open_case_db
from gbya.tools.sql_guard import (
    ROW_LIMIT,
    WHITELIST,
    SqlRejected,
    SqlTimeout,
    execute_with_timeout,
    guard,
    run_query,
)

BACKEND = Path(__file__).resolve().parents[2] / "backend"


@pytest.fixture(scope="module")
def case_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small log database built on the construction path (mode 0444 afterwards)."""
    path = tmp_path_factory.mktemp("db") / "case.duckdb"
    with build_database(path) as con:
        con.execute(
            "CREATE TABLE process_access (record_id BIGINT PRIMARY KEY, host VARCHAR, "
            "source_pid BIGINT, target_image VARCHAR)"
        )
        con.execute(
            "INSERT INTO process_access SELECT i, 'wkstn-01', 1000 + i, 'lsass.exe' "
            "FROM range(1, 3001) t(i)"
        )
        con.execute("CREATE TABLE network (record_id BIGINT PRIMARY KEY, dst_ip VARCHAR)")
        con.execute("INSERT INTO network VALUES (1, '203.0.113.7')")
        con.execute("CREATE TABLE raw_events (record_id BIGINT PRIMARY KEY, json VARCHAR)")
    (path.parent / "secret.csv").write_text("a,b\n1,2\n")
    return path


# ---------------------------------------------------------------- layer 1: accepted


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM process_access",
        "select record_id, source_pid from process_access where target_image ilike '%lsass%'",
        "FROM process_access",
        "SELECT record_id FROM process_access UNION SELECT record_id FROM network",
        "WITH x AS (SELECT * FROM process_access) SELECT * FROM x",
        "SELECT p.record_id FROM process_access p JOIN network n ON p.record_id = n.record_id",
        "SELECT * FROM (SELECT * FROM process_access) t",
        "SELECT count(*), host FROM process_access GROUP BY host",
        "SELECT * FROM raw_events WHERE json LIKE '%mimikatz%'",
    ],
)
def test_accepts_read_only_selects(sql: str) -> None:
    q = guard(sql)
    assert q.wrapped_sql.startswith("SELECT * FROM (") and q.wrapped_sql.endswith(
        f") LIMIT {ROW_LIMIT}"
    )


def test_comment_cannot_escape_the_wrapper() -> None:
    q = guard("SELECT * FROM process_access -- ; DROP TABLE network")
    assert "--" not in q.wrapped_sql and q.wrapped_sql.endswith(") LIMIT 50")


# ---------------------------------------------------------------- layer 1: rejected (>= 20)

NEGATIVE = [
    "INSERT INTO process_access VALUES (1, 'x', 1, 'y')",
    "UPDATE process_access SET host = 'x'",
    "DELETE FROM process_access",
    "DROP TABLE process_access",
    "CREATE TABLE x AS SELECT * FROM process_access",
    "ALTER TABLE process_access ADD COLUMN x INT",
    "SELECT 1; SELECT 2",
    "SELECT * FROM process_access; DROP TABLE network",
    "SELECT * FROM read_csv_auto('secret.csv')",
    "SELECT * FROM read_csv('secret.csv')",
    "SELECT * FROM 'secret.csv'",
    "SELECT * FROM glob('*')",
    "ATTACH 'other.db' AS o",
    "PRAGMA table_info('process_access')",
    "COPY process_access TO 'out.csv'",
    "INSTALL httpfs",
    "LOAD httpfs",
    "SET enable_external_access = true",
    "WITH d AS (DELETE FROM process_access RETURNING *) SELECT * FROM d",
    "WITH i AS (INSERT INTO network VALUES (2, 'x') RETURNING *) SELECT * FROM i",
    "SELECT * FROM unknown_table",
    "SELECT * FROM main.process_access",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM duckdb_settings()",
    "SELECT getenv('HOME')",
    "SELECT system('id')",
    "SELECT query('SELECT 1')",
    "SELECT current_setting('enable_external_access')",
    "SELECT * FROM range(10)",
    "SELECT * FROM process_access INTERSECT SELECT * FROM process_access",
    "DESCRIBE process_access",
    "SHOW TABLES",
    "CALL pragma_version()",
    "VALUES (1)",
    "SELECT * INTO newtab FROM process_access",
    'SELECT * FROM "process_access"',  # quoted identifiers are not plain names
    "THIS IS NOT SQL",
    "",
]


@pytest.mark.parametrize("sql", NEGATIVE)
def test_layer1_rejects(sql: str) -> None:
    with pytest.raises(SqlRejected) as exc:
        guard(sql)
    assert exc.value.code == "SQL_REJECTED" and exc.value.message


def test_negative_suite_is_large_enough() -> None:
    assert len(NEGATIVE) >= 20  # plan §I.1


def test_whitelist_is_the_log_tables() -> None:
    assert {
        "process_create",
        "process_access",
        "network",
        "registry",
        "file",
        "logon",
        "share_access",
        "raw_events",
    } == WHITELIST


# ---------------------------------------------------------------- layer 2 (layer 1 bypassed)

LAYER2_ATTACKS = [
    "SELECT * FROM read_csv_auto('{dir}/secret.csv')",
    "ATTACH '{dir}/other.duckdb' AS o",
    "COPY process_access TO '{dir}/out.csv'",
    "INSTALL httpfs",
    "SET enable_external_access = true",
    # additional configuration changes
    "SET lock_configuration = false",
    "SET memory_limit = '8GB'",
    "LOAD httpfs",
    "CREATE TABLE x (a INT)",
    "INSERT INTO network VALUES (99, 'x')",
]


@pytest.mark.parametrize("attack", LAYER2_ATTACKS)
def test_layer2_blocks_with_layer1_bypassed(case_db: Path, attack: str) -> None:
    con = open_case_db(case_db)
    try:
        with pytest.raises(duckdb.Error):
            con.execute(attack.format(dir=case_db.parent))
    finally:
        con.close()
    assert not (case_db.parent / "out.csv").exists()


def test_plain_read_only_connection_is_not_enough(case_db: Path) -> None:
    """The plan's [V] finding: read-only alone still lets read_csv_auto read local files."""
    con = duckdb.connect(str(case_db), read_only=True)
    try:
        rows = con.execute(f"SELECT * FROM read_csv_auto('{case_db.parent}/secret.csv')").fetchall()
    finally:
        con.close()
    assert rows == [(1, 2)]


def test_hardening_settings_are_applied(case_db: Path) -> None:
    con = open_case_db(case_db)
    try:
        settings = dict(
            con.execute(
                "SELECT name, value FROM duckdb_settings() WHERE name IN ("
                "'enable_external_access', 'autoinstall_known_extensions', "
                "'autoload_known_extensions', 'threads', 'lock_configuration')"
            ).fetchall()
        )
    finally:
        con.close()
    assert settings == {
        "enable_external_access": "false",
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "threads": "2",
        "lock_configuration": "true",
    }
    assert HARDENING[-1] == "SET lock_configuration = true"  # locked last


def test_pinned_duckdb_version() -> None:
    assert duckdb.__version__ == "1.5.6"  # layer-2 behaviour verified on this version


def test_open_case_db_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CaseDbNotFound):
        open_case_db(tmp_path / "nope.duckdb")


# ---------------------------------------------------------------- execution and timeout


def test_run_query_applies_limit(case_db: Path) -> None:
    con = open_case_db(case_db)
    try:
        q, res = run_query(con, "SELECT record_id, source_pid FROM process_access ORDER BY 1")
    finally:
        con.close()
    assert res.columns == ["record_id", "source_pid"]
    assert len(res.rows) == ROW_LIMIT and res.rows[0] == (1, 1001)
    assert q.inner_sql.lower().startswith("select")


def test_timeout_interrupts_long_query(case_db: Path) -> None:
    con = open_case_db(case_db)
    try:
        with pytest.raises(SqlTimeout):
            run_query(
                con,
                "SELECT count(*) FROM process_access a, process_access b, process_access c",
                timeout_s=0.5,
            )
        # the connection is still usable afterwards
        assert execute_with_timeout(con, "SELECT 1").rows == [(1,)]
    finally:
        con.close()


# ---------------------------------------------------------------- database boundary (§D.1.1)


def test_write_through_execution_factory_fails(case_db: Path, tmp_path: Path) -> None:
    con = open_case_db(case_db)
    try:
        for stmt in (
            "CREATE TABLE t (a INT)",
            "DELETE FROM network",
            "UPDATE network SET dst_ip='x'",
        ):
            with pytest.raises(duckdb.Error):
                con.execute(stmt)
    finally:
        con.close()


def test_built_file_is_read_only_on_disk(case_db: Path) -> None:
    assert case_db.stat().st_mode & 0o777 == 0o444


def test_duckdb_connect_only_in_the_two_factories() -> None:
    allowed = {BACKEND / "gbya/data/build_db.py", BACKEND / "gbya/data/connection.py"}
    offenders = [
        str(p.relative_to(BACKEND))
        for p in (BACKEND / "gbya").rglob("*.py")
        if p not in allowed and re.search(r"duckdb\s*\.\s*connect\b", p.read_text())
    ]
    assert offenders == []


EXECUTION_PACKAGES = [
    "gbya.tools",
    "gbya.gate",
    "gbya.agent",
    "gbya.retrieval",
    "gbya.scoring",
    "gbya.experiments",
    "gbya.api",
]


def _modules() -> list[str]:
    names = ["gbya.worker", "gbya.data.connection"]
    for pkg_name in EXECUTION_PACKAGES:
        pkg = importlib.import_module(pkg_name)
        names.append(pkg_name)
        names += [m.name for m in pkgutil.walk_packages(pkg.__path__, prefix=pkg_name + ".")]
    return sorted(set(names))


def test_execution_modules_never_import_build_db() -> None:
    """Import every execution-path module in a fresh interpreter; build_db must stay unloaded."""
    code = (
        "import importlib, sys\n"
        "bad = []\n"
        "for name in sys.argv[1:]:\n"
        "    importlib.import_module(name)\n"
        "    if 'gbya.data.build_db' in sys.modules:\n"
        "        bad.append(name); break\n"
        "print(bad)\n"
    )
    for name in _modules():
        out = subprocess.run(
            [sys.executable, "-c", code, name], capture_output=True, text=True, check=True
        ).stdout.strip()
        assert out == "[]", f"{name} imports gbya.data.build_db (directly or transitively)"
