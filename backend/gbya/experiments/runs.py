"""Creating runs with a purpose, and the research-eligibility rule (NFR-12; T3.5 extends).

A run is tagged ``research`` only if every one of its cases is in the frozen case set
(``cases/FROZEN.json``, written by ``make freeze-cases`` in T4.10). Runs on development data or on
the hand-made fixture are tagged ``development`` / ``fixture`` and can never feed research
results; the analysis API (T7.2) accepts only research, non-replay runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.orm import Session

from gbya.config import REPO_ROOT
from gbya.errors import Unprocessable
from gbya.store.models import Case, Run

FROZEN = REPO_ROOT / "cases" / "FROZEN.json"
Purpose = Literal["research", "development", "fixture", "demo"]


class ResearchRunRefused(Unprocessable):
    code = "RESEARCH_RUN_REFUSED"


def frozen_hashes(path: Path = FROZEN) -> tuple[str, set[str]] | None:
    """(case_set_hash, content hashes) of the frozen case set, or None before the freeze."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    return str(data["case_set_hash"]), set(data["content_hashes"])


def create_run(
    session: Session,
    *,
    experiment: int,
    config: dict[str, Any],
    config_hash: str,
    purpose: Purpose,
    case_ids: Iterable[str],
    replay: bool = False,
    frozen_path: Path = FROZEN,
) -> Run:
    ids = list(case_ids)
    case_set_hash = None
    if purpose == "research":
        if replay:
            raise ResearchRunRefused("Replay runs can never be research runs")
        frozen = frozen_hashes(frozen_path)
        if frozen is None:
            raise ResearchRunRefused(
                "No frozen case set: research runs need `make freeze-cases` (T4.10)",
                hint="Use purpose 'development' or 'fixture'",
            )
        case_set_hash, hashes = frozen
        rows = [session.get(Case, cid) for cid in ids]
        outside = [
            cid
            for cid, r in zip(ids, rows, strict=True)
            if r is None or r.content_hash not in hashes
        ]
        if outside:
            raise ResearchRunRefused(
                f"{len(outside)} case(s) are not in the frozen case set",
                details={"cases": outside[:20]},
            )
    run = Run(
        experiment=experiment,
        config=config,
        config_hash=config_hash,
        case_set_hash=case_set_hash,
        purpose=purpose,
        replay=replay,
    )
    session.add(run)
    session.flush()
    return run


def research_eligible(run: Run) -> bool:
    """True only for research runs on the frozen set that are not replays."""
    return run.purpose == "research" and not run.replay and run.case_set_hash is not None
