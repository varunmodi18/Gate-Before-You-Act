"""Scenario and case files ↔ app.db (plan T4.1, ``make import-cases``).

``import_scenario`` reads ``cases/<sid>/scenario.json`` and every ``cases/<sid>/cases/*.json`` into
the ``scenarios`` and ``cases`` tables (content hash, effective approval script, case database
path); cases of the scenario that no longer have a file are removed. ``export_case`` rebuilds the
file text from the row, so file → DB → file is byte-identical. No log database is touched here.

    python -m gbya.cases.store [scenario_id ...]       # make import-cases
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.cases.models import (
    PATCHED,
    CaseFile,
    EndToEndFile,
    ScenarioFile,
    case_path,
    content_hash,
    dump,
    e2e_path,
    effective_context,
    load_casefile,
    load_scenario,
    scenario_path,
)
from gbya.config import Settings, get_settings
from gbya.context.store import context_hash
from gbya.errors import NotFound, Unprocessable
from gbya.store.models import Case, Scenario, Window


def cases_dir(settings: Settings) -> Path:
    return settings.resolve(settings.cases_dir)


def window_db(settings: Settings, window_id: str) -> Path:
    return settings.data_dir / "duckdb" / "windows" / f"{window_id}.duckdb"


def case_db(settings: Settings, case_id: str) -> Path:
    return settings.data_dir / "duckdb" / "cases" / f"{case_id.replace(':', '_')}.duckdb"


def base_db(settings: Settings, sid: str) -> Path:
    """The window with the scenario's injection marker planted (``cases.injection``)."""
    return settings.data_dir / "duckdb" / "cases" / f"{sid}__base.duckdb"


def source_db(settings: Settings, sid: str, window_id: str, injected: bool) -> Path:
    """What a scenario's variants derive from: the base database if a marker is planted."""
    return base_db(settings, sid) if injected else window_db(settings, window_id)


def db_path_for(settings: Settings, case: CaseFile, window_id: str) -> Path:
    """Patched variants (E3-E5) have their own database; the others read the window's, or the
    scenario's base database when an injection marker is planted."""
    if case.variant in PATCHED:
        return case_db(settings, case.id)
    return source_db(settings, case.scenario_id, window_id, case.injection is not None)


def import_scenario(session: Session, settings: Settings, sid: str) -> list[str]:
    root = cases_dir(settings)
    sc = load_scenario(scenario_path(root, sid))
    window = session.get(Window, sc.window_id)
    if window is None:
        raise NotFound(f"Window {sc.window_id} is not catalogued", code="WINDOW_NOT_FOUND")
    row = session.get(Scenario, sid) or Scenario(id=sid, window_id=sc.window_id)
    row.window_id = sc.window_id
    row.split = window.split
    row.target_host = sc.target_host
    row.request_template = sc.request.model_dump(mode="json")
    row.trusted_context = sc.trusted_context.model_dump(mode="json")
    row.context_hash = context_hash(sc.trusted_context)
    row.spec = sc.model_dump(mode="json", by_alias=True)
    session.add(row)
    session.flush()

    ids = []
    for path in sorted((root / sid / "cases").glob("*.json")):
        cf = load_casefile(path)
        if cf.scenario_id != sid:
            raise Unprocessable(f"{path}: case belongs to {cf.scenario_id}", code="CASE_MISPLACED")
        ctx = effective_context(sc.trusted_context, sc.target_host, cf.r_edit)
        case = session.get(Case, cf.id) or Case(id=cf.id, scenario_id=sid)
        case.scenario_id = sid
        case.set_ = cf.set
        case.variant = cf.variant
        case.request = cf.request.model_dump(mode="json")
        case.package = cf.package.model_dump(mode="json")
        case.db_patch = cf.db_patch.model_dump(mode="json") if cf.db_patch else None
        case.r_edit = cf.r_edit.model_dump(mode="json") if cf.r_edit else None
        case.e2_basis = cf.e2_basis.model_dump(mode="json") if cf.e2_basis else None
        case.injection = cf.injection.model_dump(mode="json") if cf.injection else None
        case.labels = cf.labels.model_dump(mode="json", by_alias=True)
        case.approval_script = ctx.approval_script.model_dump(mode="json")
        case.case_db_path = str(db_path_for(settings, cf, sc.window_id))
        case.content_hash = content_hash(cf, ctx)
        session.add(case)
        ids.append(cf.id)
    for stale in session.scalars(select(Case).where(Case.scenario_id == sid, Case.id.not_in(ids))):
        session.delete(stale)
    session.flush()
    return ids


def export_case(session: Session, case_id: str) -> CaseFile:
    row = session.get(Case, case_id)
    if row is None:
        raise NotFound(f"No case {case_id}", code="CASE_NOT_FOUND")
    return CaseFile.model_validate({
        "id": row.id, "scenario_id": row.scenario_id, "set": row.set_, "variant": row.variant,
        "request": row.request, "package": row.package, "db_patch": row.db_patch,
        "r_edit": row.r_edit, "labels": row.labels or {}, "e2_basis": row.e2_basis,
        "injection": row.injection,
    })  # fmt: skip


def export_scenario(session: Session, sid: str) -> ScenarioFile:
    row = session.get(Scenario, sid)
    if row is None or row.spec is None:
        raise NotFound(f"No scenario {sid}", code="SCENARIO_NOT_FOUND")
    return ScenarioFile.model_validate(row.spec)


def write_case(settings: Settings, case: CaseFile) -> Path:
    path = case_path(cases_dir(settings), case.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(case), encoding="utf-8")
    return path


def write_scenario(settings: Settings, sc: ScenarioFile) -> Path:
    path = scenario_path(cases_dir(settings), sc.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(sc), encoding="utf-8")
    return path


def scenario_ids(settings: Settings) -> list[str]:
    root = cases_dir(settings)
    return sorted(p.parent.name for p in root.glob("*/scenario.json"))


def main(argv: list[str]) -> None:
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    sids = argv or scenario_ids(settings)
    with session_scope(factory) as s:
        for sid in sids:
            ids = import_scenario(s, settings, sid)
            print(f"{sid}: {len(ids)} cases imported")


if __name__ == "__main__":
    main(sys.argv[1:])


def write_e2e(settings: Settings, e: EndToEndFile) -> Path:
    path = e2e_path(cases_dir(settings), e.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(e.model_dump_json(indent=2, by_alias=True) + "\n", encoding="utf-8")
    return path


def e2e_ids(settings: Settings) -> list[str]:
    root = cases_dir(settings)
    return sorted(p.parent.name for p in root.glob("*/e2e.json"))


def import_e2e(session: Session, settings: Settings, sid: str) -> str:
    """An end-to-end window (Exp 3): a scenario row and its one case ``<sid>:X`` (set ``X``),
    reading the window's own database (no patch, no prefix)."""
    from gbya.cases.models import e2e_content_hash, e2e_path, load_e2e

    e = load_e2e(e2e_path(cases_dir(settings), sid))
    if e.id != sid:
        raise Unprocessable(f"e2e.json of {sid} names {e.id}", code="CASE_MISPLACED")
    window = session.get(Window, e.window_id)
    if window is None:
        raise NotFound(f"Window {e.window_id} is not catalogued", code="WINDOW_NOT_FOUND")
    row = session.get(Scenario, sid) or Scenario(id=sid, window_id=e.window_id)
    row.window_id, row.split, row.target_host = e.window_id, window.split, e.target_host
    row.request_template = e.request.model_dump(mode="json")
    row.trusted_context = e.trusted_context.model_dump(mode="json")
    row.context_hash = context_hash(e.trusted_context)
    row.spec = {"kind": "end_to_end", **e.model_dump(mode="json", by_alias=True)}
    session.add(row)
    session.flush()
    case = session.get(Case, e.case_id) or Case(id=e.case_id, scenario_id=sid)
    case.scenario_id, case.set_, case.variant = sid, "X", "X"
    case.request = e.request.model_dump(mode="json")
    case.package = {}
    case.db_patch = case.r_edit = case.e2_basis = None
    case.labels = e.labels.model_dump(mode="json", by_alias=True)
    case.approval_script = e.trusted_context.approval_script.model_dump(mode="json")
    case.case_db_path = str(source_db(settings, sid, e.window_id, e.injection is not None))
    case.injection = e.injection.model_dump(mode="json") if e.injection else None
    case.content_hash = e2e_content_hash(e)
    session.add(case)
    session.flush()
    return e.case_id
