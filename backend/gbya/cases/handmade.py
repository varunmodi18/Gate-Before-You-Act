"""Import the hand-made fixture cases (``data/fixtures/handmade/cases.json``) into app.db.

Used by the Exp 1 core test (T2.6), the Gate Playground (T2.7) and the Playwright suite until real
scenarios exist (M4). The general case importer (``make import-cases``, T4.1) replaces this.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from gbya.config import REPO_ROOT
from gbya.context.models import TrustedContext
from gbya.context.store import context_hash
from gbya.store.models import Case, Scenario, Window

FIXTURE = REPO_ROOT / "data" / "fixtures" / "handmade" / "cases.json"


def load_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    TrustedContext.model_validate(data["scenario"]["trusted_context"])  # fail early if invalid
    return data


def insert_fixture(session: Session, case_db_path: Path, path: Path = FIXTURE) -> list[str]:
    """Insert (or replace) the fixture's window, scenario and cases; returns the case ids."""
    data = load_fixture(path)
    wid = data["window_id"]
    if session.get(Window, wid) is None:
        session.add(Window(id=wid, title="Mini fixture window (hand-made, OTRF format)"))
        session.flush()
    sc = data["scenario"]
    ctx = TrustedContext.model_validate(sc["trusted_context"])
    scenario = session.get(Scenario, sc["id"]) or Scenario(id=sc["id"], window_id=wid)
    scenario.window_id = wid
    scenario.target_host = sc["target_host"]
    scenario.trusted_context = sc["trusted_context"]
    scenario.context_hash = context_hash(ctx)
    scenario.status = "draft"
    session.add(scenario)
    session.flush()
    ids = []
    for c in data["cases"]:
        row = session.get(Case, c["id"]) or Case(id=c["id"], scenario_id=sc["id"])
        row.scenario_id = sc["id"]
        row.set_ = c["set"]
        row.variant = c["variant"]
        row.request = c["request"]
        row.package = c["package"]
        row.case_db_path = str(case_db_path)
        row.labels = c["labels"]
        session.add(row)
        ids.append(c["id"])
    session.flush()
    return ids
