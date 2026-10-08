"""Windows API (plan §F.5, T1.4): catalogue listing, table rows, record detail, guarded SQL.

Every log database is opened through ``gbya.data.connection.open_case_db`` (hardened, read-only).
User-supplied SQL goes through the same guard as the agent's ``sql_query`` tool. Table browsing
builds its own parameterised SQL from validated table and column names.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.api.deps import get_session, get_settings
from gbya.config import Settings
from gbya.data.catalogue import TACTIC_NAMES
from gbya.data.connection import open_case_db
from gbya.data.fieldmap import TABLES
from gbya.errors import BadRequest, NotFound
from gbya.store.models import Window
from gbya.tools.sql_guard import run_query

router = APIRouter(prefix="/windows", tags=["windows"])

BROWSABLE = [*TABLES, "raw_events"]
MAX_PAGE = 500
TACTIC_IDS = {name: tid for tid, name in TACTIC_NAMES.items()}


class WindowSummary(BaseModel):
    id: str
    title: str
    techniques: list[str]
    tactics: list[str]
    tactic_names: list[str]
    hosts: list[str]
    event_count: int | None
    split: str | None
    ingest_status: str | None


class TableRows(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    total: int


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=10_000)


class QueryRows(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    sql: str  # the guarded, wrapped statement that actually ran


class RecordDetail(BaseModel):
    record_id: int
    table: str | None  # normalised table, or None when the event is only in raw_events
    normalised: dict[str, Any] | None
    raw: Any


def _json_value(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _summary(w: Window) -> WindowSummary:
    return WindowSummary(
        id=w.id,
        title=w.title,
        techniques=list(w.techniques or []),
        tactics=list(w.tactics or []),
        tactic_names=[TACTIC_NAMES.get(t, t) for t in (w.tactics or [])],
        hosts=list(w.hosts or []),
        event_count=w.event_count,
        split=w.split,
        ingest_status=w.ingest_status,
    )


def _window(session: Session, window_id: str) -> Window:
    w = session.get(Window, window_id)
    if w is None:
        raise NotFound(f"No window {window_id}", code="WINDOW_NOT_FOUND")
    return w


def _db_path(w: Window, settings: Settings) -> Path:
    if not w.duckdb_path:
        raise NotFound(
            f"Window {w.id} has no log database (status: {w.ingest_status})",
            code="WINDOW_NOT_INGESTED",
            hint="Run `make normalise`",
        )
    path = settings.resolve(Path(w.duckdb_path))
    if not path.is_file():
        raise NotFound(
            f"Log database for {w.id} is missing",
            code="WINDOW_NOT_INGESTED",
            hint="Run `make normalise`",
        )
    return path


@router.get("", response_model=list[WindowSummary])
def list_windows(
    session: Annotated[Session, Depends(get_session)],
    split: str | None = None,
    tactic: str | None = None,
    q: str | None = None,
) -> list[WindowSummary]:
    rows = session.scalars(select(Window).order_by(Window.id)).all()
    out = []
    tactic_id = TACTIC_IDS.get(tactic, tactic) if tactic else None
    needle = q.lower() if q else None
    for w in rows:
        if split and w.split != split:
            continue
        if tactic_id and tactic_id not in (w.tactics or []):
            continue
        if (
            needle
            and needle not in w.title.lower()
            and needle not in w.id.lower()
            and not any(needle in t.lower() for t in (w.techniques or []))
        ):
            continue
        out.append(_summary(w))
    return out


@router.get("/{window_id}", response_model=WindowSummary)
def get_window(window_id: str, session: Annotated[Session, Depends(get_session)]) -> WindowSummary:
    return _summary(_window(session, window_id))


def _parse_filters(filters: list[str], columns: list[str]) -> tuple[str, list[Any]]:
    """``column:text`` → case-insensitive "contains" on that column (combined with AND)."""
    clauses, params = [], []
    for f in filters:
        col, sep, text = f.partition(":")
        if not sep or col not in columns:
            raise BadRequest(
                f"Bad filter {f!r}",
                code="BAD_FILTER",
                hint="Use column:text with a column of this table",
                details={"columns": columns},
            )
        clauses.append(f'CAST("{col}" AS VARCHAR) ILIKE ?')
        params.append(f"%{text}%")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


@router.get("/{window_id}/tables/{table}", response_model=TableRows)
def table_rows(
    window_id: str,
    table: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    filter: Annotated[list[str] | None, Query()] = None,
) -> TableRows:
    if table not in BROWSABLE:
        raise NotFound(f"No table {table}", code="TABLE_NOT_FOUND", details={"tables": BROWSABLE})
    path = _db_path(_window(session, window_id), settings)
    con = open_case_db(path)
    try:
        columns = [r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()]
        where, params = _parse_filters(filter or [], columns)
        total = con.execute(f'SELECT count(*) FROM "{table}"{where}', params).fetchone()
        rows = con.execute(
            f'SELECT * FROM "{table}"{where} ORDER BY record_id LIMIT ? OFFSET ?',
            [*params, limit, offset],
        ).fetchall()
    finally:
        con.close()
    return TableRows(
        columns=columns,
        rows=[[_json_value(v) for v in r] for r in rows],
        total=int(total[0]) if total else 0,
    )


@router.get("/{window_id}/records/{record_id}", response_model=RecordDetail)
def record_detail(
    window_id: str,
    record_id: int,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RecordDetail:
    path = _db_path(_window(session, window_id), settings)
    con = open_case_db(path)
    try:
        raw = con.execute("SELECT json FROM raw_events WHERE record_id = ?", [record_id]).fetchone()
        if raw is None:
            raise NotFound(f"No record {record_id} in {window_id}", code="RECORD_NOT_FOUND")
        for table in TABLES:
            cur = con.execute(f'SELECT * FROM "{table}" WHERE record_id = ?', [record_id])
            row = cur.fetchone()
            if row is not None:
                cols = [d[0] for d in cur.description or []]
                normalised = {c: _json_value(v) for c, v in zip(cols, row, strict=True)}
                return RecordDetail(
                    record_id=record_id, table=table, normalised=normalised, raw=json.loads(raw[0])
                )
    finally:
        con.close()
    return RecordDetail(record_id=record_id, table=None, normalised=None, raw=json.loads(raw[0]))


@router.post("/{window_id}/query", response_model=QueryRows)
def guarded_query(
    window_id: str,
    body: QueryRequest,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryRows:
    path = _db_path(_window(session, window_id), settings)
    con = open_case_db(path)
    try:
        guarded, result = run_query(con, body.sql)
    finally:
        con.close()
    return QueryRows(
        columns=result.columns,
        rows=[[_json_value(v) for v in r] for r in result.rows],
        sql=guarded.wrapped_sql,
    )
