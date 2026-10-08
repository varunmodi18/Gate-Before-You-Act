"""Scenario Studio API (plan §E.1 row 3, §F.5; T4.4 validate, T4.5 studio).

The API reads and writes scenario and case **files**; it never builds or patches a log database.
Variant generation runs the construction-path command line as a short-lived subprocess (§D.1.1).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from gbya.api.deps import get_deps, get_session, get_settings
from gbya.cases.models import (
    VARIANTS,
    ScenarioFile,
    case_path,
    dump,
    load_casefile,
    load_scenario,
    scenario_path,
)
from gbya.cases.store import case_db, db_path_for, window_db
from gbya.cases.validator import validate_scenario
from gbya.config import Settings
from gbya.data.connection import open_case_db
from gbya.data.fieldmap import TABLES
from gbya.errors import Conflict, NotFound, Unprocessable
from gbya.experiments.runner import Deps
from gbya.store.models import Scenario, Window

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _require(settings: Settings, sid: str) -> None:
    if not scenario_path(settings.resolve(settings.cases_dir), sid).is_file():
        raise NotFound(f"No scenario {sid}", code="SCENARIO_NOT_FOUND")


@router.post("/{sid}/validate")
def validate(
    sid: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    deps: Annotated[Deps, Depends(get_deps)],
) -> dict[str, Any]:
    """Checks a-i and Set R (§D.11); label checks are ``pending`` until annotation."""
    _require(settings, sid)
    from gbya.cases.models import load_scenario

    sc = load_scenario(scenario_path(settings.resolve(settings.cases_dir), sid))
    window = session.get(Window, sc.window_id)
    meta = (
        {"techniques": window.techniques, "tactics": window.tactics, "split": window.split}
        if window
        else {}
    )
    report = validate_scenario(settings, sid, deps.counter, window_meta=meta)
    return {**report.to_json(), "tokenizer": deps.counter.name}


def _root(settings: Settings) -> Path:
    return settings.resolve(settings.cases_dir)


class ScenarioSummary(BaseModel):
    id: str
    window_id: str | None
    target_host: str | None
    status: str
    cases: list[str]
    error: str | None = None


@router.get("", response_model=list[ScenarioSummary])
def list_scenarios(
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[ScenarioSummary]:
    out = []
    for path in sorted(_root(settings).glob("*/scenario.json")):
        sid = path.parent.name
        cases = sorted(p.stem for p in (path.parent / "cases").glob("*.json"))
        row = session.get(Scenario, sid)
        try:
            sc = load_scenario(path)
            out.append(
                ScenarioSummary(
                    id=sid,
                    window_id=sc.window_id,
                    target_host=sc.target_host,
                    status=row.status if row else "draft",
                    cases=cases,
                )
            )
        except ValidationError as exc:
            out.append(
                ScenarioSummary(
                    id=sid,
                    window_id=None,
                    target_host=None,
                    status="draft",
                    cases=cases,
                    error=str(exc)[:300],
                )
            )
    return out


class ScenarioCreate(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    window_id: str


def starter(settings: Settings, window: Window, sid: str) -> ScenarioFile:
    """A valid starting point from the window: its hosts as tier-2 assets, the primary host as
    the target, and its first event there as the (placeholder) E1 citation."""
    hosts = [str(h) for h in (window.hosts or [])] or ["unknown-host"]
    target = hosts[0]
    firsts: list[int] = []
    db = settings.resolve(window_db(settings, window.id))
    if db.is_file():
        con = open_case_db(db)
        try:
            for t in TABLES:
                q = f'SELECT min(record_id) FROM "{t}" WHERE host = ?'
                row = con.execute(q, [target]).fetchone()
                if row and row[0] is not None:
                    firsts.append(int(row[0]))
        finally:
            con.close()
    first = min(firsts, default=1)
    return ScenarioFile.model_validate(
        {
            "id": sid,
            "window_id": window.id,
            "target_host": target,
            "request": {
                "objective": "contain_host",
                "target": {"host": target},
                "text": f"Contain host {target}.",
            },
            "trusted_context": {
                "schema_version": 1,
                "assets": [{"host": h, "role": "workstation", "tier": 2} for h in hosts],
                "identities": [],
                "network": {"internal_cidrs": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]},
                "approval_script": {"mode": "unreachable"},
                "change_tickets": [],
            },
            "e1": {
                "tool": "isolate_host",
                "args": {"host": target},
                "cited": [first],
                "technique_claimed": (window.techniques or [None])[0],
                "rationale": f"These records show malicious activity on {target}; isolate it now.",
            },
            "suspicious_record_ids": [first],
            "notes": "Starter generated from the window: replace the placeholders.",
        }
    )


@router.post("", status_code=201)
def create(
    body: ScenarioCreate,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    window = session.get(Window, body.window_id)
    if window is None:
        raise NotFound(f"No window {body.window_id}", code="WINDOW_NOT_FOUND")
    path = scenario_path(_root(settings), body.id)
    if path.exists():
        raise Conflict(f"Scenario {body.id} exists", code="SCENARIO_EXISTS")
    sc = starter(settings, window, body.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(sc), encoding="utf-8")
    return {"id": body.id, "scenario": sc.model_dump(mode="json", by_alias=True)}


@router.get("/{sid}")
def get_scenario(
    sid: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    _require(settings, sid)
    path = scenario_path(_root(settings), sid)
    row = session.get(Scenario, sid)
    cases = []
    for p in sorted((path.parent / "cases").glob("*.json")):
        cf = load_casefile(p)
        cases.append(
            {
                "id": cf.id,
                "variant": cf.variant,
                "cited": cf.package.cited,
                "db_patch": cf.db_patch is not None,
                "labelled": cf.labels.labelled,
            }
        )
    return {
        "id": sid,
        "status": row.status if row else "draft",
        "scenario": json.loads(path.read_text()),
        "cases": cases,
    }


@router.put("/{sid}")
def save(
    sid: str,
    body: dict[str, Any],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Validate against the scenario schema and write ``scenario.json`` (frozen: refused)."""
    _require(settings, sid)
    row = session.get(Scenario, sid)
    if row is not None and row.status == "frozen":
        raise Conflict(f"Scenario {sid} is frozen", code="SCENARIO_FROZEN")
    try:
        sc = ScenarioFile.model_validate({**body, "id": sid})
    except ValidationError as exc:
        raise Unprocessable(
            "The scenario does not match the schema",
            code="SCENARIO_INVALID",
            details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]},
        ) from exc
    scenario_path(_root(settings), sid).write_text(dump(sc), encoding="utf-8")
    return {"id": sid, "scenario": sc.model_dump(mode="json", by_alias=True)}


def _subprocess_env(settings: Settings) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env.update(
        {
            "GBYA_ENV": settings.env,
            "GBYA_APP_DB_PATH": str(settings.resolve(settings.app_db_path)),
            "GBYA_DATA_DIR": str(settings.resolve(settings.data_dir)),
            "GBYA_CASES_DIR": str(settings.resolve(settings.cases_dir)),
            "GBYA_LOG_DIR": str(settings.resolve(settings.log_dir)),
        }
    )
    return env


@router.post("/{sid}/generate")
def generate_variants(
    sid: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Build the variants through the construction-path command line, as a short-lived
    subprocess (§D.1.1): the API never patches a database itself."""
    _require(settings, sid)
    row = session.get(Scenario, sid)
    if row is not None and row.status == "frozen":
        raise Conflict(f"Scenario {sid} is frozen", code="SCENARIO_FROZEN")
    proc = subprocess.run(
        [sys.executable, "-m", "gbya.cases.cli", "generate", sid],
        capture_output=True,
        text=True,
        env=_subprocess_env(settings),
        timeout=600,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-12:]
        raise Unprocessable(
            "Variant generation failed", code="GENERATE_FAILED", details={"output": tail}
        )
    out = proc.stdout
    report: dict[str, Any] = json.loads(out[out.index("{") :])
    return report


def _records(db: Path) -> dict[int, dict[str, Any]]:
    con = open_case_db(db)
    try:
        out: dict[int, dict[str, Any]] = {
            int(rid): {"table": None, "raw": raw}
            for rid, raw in con.execute("SELECT record_id, json FROM raw_events").fetchall()
        }
        for t in TABLES:
            cur = con.execute(f'SELECT * FROM "{t}"')
            cols = [d[0] for d in cur.description or []]
            for r in cur.fetchall():
                d = dict(zip(cols, r, strict=True))
                out[int(d["record_id"])] |= {
                    "table": t,
                    "row": {
                        k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()
                    },
                }
        return out
    finally:
        con.close()


@router.get("/{sid}/cases/{variant}")
def case_detail(
    sid: str, variant: str, settings: Annotated[Settings, Depends(get_settings)]
) -> dict[str, Any]:
    """The case file, its prefix and, for E3-E5, the database diff against the window."""
    _require(settings, sid)
    if variant not in VARIANTS:
        raise NotFound(f"No variant {variant}", code="CASE_NOT_FOUND")
    root = _root(settings)
    path = case_path(root, f"{sid}:{variant}")
    if not path.is_file():
        raise NotFound(f"Case {sid}:{variant} is not generated", code="CASE_NOT_FOUND")
    cf = load_casefile(path)
    sc = load_scenario(scenario_path(root, sid))
    prefix_file = root / sid / "prefixes" / f"{variant}.json"
    diff = None
    if cf.db_patch is not None:
        before = _records(settings.resolve(window_db(settings, sc.window_id)))
        after = _records(settings.resolve(case_db(settings, cf.id)))
        changed = []
        for rid in sorted(set(before) & set(after)):
            b, a = before[rid].get("row") or {}, after[rid].get("row") or {}
            fields = {
                k: [b.get(k), a.get(k)] for k in sorted(set(b) | set(a)) if b.get(k) != a.get(k)
            }
            if fields or before[rid]["raw"] != after[rid]["raw"]:
                changed.append({"record_id": rid, "table": after[rid]["table"], "fields": fields})
        diff = {
            "removed": sorted(set(before) - set(after)),
            "added": [
                {"record_id": r, "table": after[r]["table"], "row": after[r].get("row")}
                for r in sorted(set(after) - set(before))
            ],
            "changed": changed,
        }
    return {
        "case": json.loads(path.read_text()),
        "db": str(db_path_for(settings, cf, sc.window_id)),
        "prefix": json.loads(prefix_file.read_text()) if prefix_file.is_file() else None,
        "diff": diff,
    }
