"""Reopen one submitted annotation (team lead only; decision 2026-11-08).

``make reopen-annotation SID=… ROLE=A BY="…" REASON="…"`` sets one submitted annotation back to
draft, so that its annotator can correct and resubmit it through the Annotate API, instead of
anyone editing app.db by hand. Every reopen is appended to ``cases/ANNOTATION_REOPENS.json``
(who, when, sid, role, reason, the annotator's name and a hash of the labels before), which is
committed with the case files.

Refused when the scenario is adjudicated or frozen, when the annotation is not submitted, and
whenever **any** role-B annotation or adjudication exists for the scenario: blindness and
agreement are never affected by a reopen. The labels themselves are not changed here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from gbya.config import Settings
from gbya.errors import GbyaError
from gbya.store.models import Adjudication, Annotation, Case, Scenario, utcnow

AUDIT_FILE = "ANNOTATION_REOPENS.json"


class ReopenRefused(GbyaError):
    code = "REOPEN_REFUSED"
    http_status = 409


def labels_hash(labels: dict[str, Any] | None) -> str:
    """SHA-256 of the labels as canonical JSON (sorted keys, no whitespace)."""
    text = json.dumps(labels or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def reopen_annotation(
    session: Session, settings: Settings, sid: str, role: str, *, by: str, reason: str
) -> dict[str, Any]:
    """Set the submitted annotation of ``role`` for ``sid`` back to draft and log it."""
    if role not in ("A", "B"):
        raise ReopenRefused(f"role must be A or B, not {role!r}")
    if not by.strip() or not reason.strip():
        raise ReopenRefused("a reopen needs the team lead's name (BY) and a reason (REASON)")
    row = session.get(Scenario, sid)
    if row is None:
        raise ReopenRefused(f"no scenario {sid}")
    if row.status in ("adjudicated", "frozen"):
        raise ReopenRefused(f"{sid} is {row.status}; its labels can no longer be reopened")
    b = session.scalars(
        select(Annotation).where(Annotation.scenario_id == sid, Annotation.annotator_role == "B")
    ).first()
    if b is not None:
        raise ReopenRefused(f"{sid} has a role-B annotation; a reopen would affect agreement")
    case_ids = list(session.scalars(select(Case.id).where(Case.scenario_id == sid)))
    adj = session.scalars(
        select(Adjudication).where(Adjudication.ref_id.in_([*case_ids, sid]))
    ).first()
    if adj is not None:
        raise ReopenRefused(f"{sid} has an adjudication; it can no longer be reopened")
    a = session.scalars(
        select(Annotation).where(Annotation.scenario_id == sid, Annotation.annotator_role == role)
    ).first()
    if a is None or a.submitted_at is None:
        raise ReopenRefused(f"{sid}: no submitted annotation for role {role}")
    entry = {
        "at": utcnow().isoformat(),
        "by": by.strip(),
        "scenario_id": sid,
        "role": role,
        "annotator_name": a.annotator_name,
        "reason": reason.strip(),
        "submitted_at_before": a.submitted_at.isoformat(),
        "labels_sha256_before": labels_hash(a.labels),
    }
    a.submitted_at = None
    session.flush()
    _append_audit(settings, entry)
    return entry


def _append_audit(settings: Settings, entry: dict[str, Any]) -> None:
    path = Path(settings.resolve(settings.cases_dir)) / AUDIT_FILE
    entries: list[dict[str, Any]] = json.loads(path.read_text()) if path.is_file() else []
    entries.append(entry)
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> None:
    from gbya.config import get_settings
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    ap = argparse.ArgumentParser(description="Reopen one submitted annotation (team lead only)")
    ap.add_argument("--sid", required=True)
    ap.add_argument("--role", required=True, choices=["A", "B"])
    ap.add_argument("--by", required=True, help="the team lead's name")
    ap.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        entry = reopen_annotation(s, settings, args.sid, args.role, by=args.by, reason=args.reason)
    print(json.dumps(entry, indent=1))


if __name__ == "__main__":
    main()
