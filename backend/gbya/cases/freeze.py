"""Freeze the case set (plan T4.10, NFR-12). Run by the team only, at T4.10:

    make freeze-cases VERSION=cases-v1        # python -m gbya.cases.freeze --version cases-v1

Preconditions (each refusal names what is missing):
* the validator runs with the **model tokenizer** (budget check g in model tokens);
* every scenario under ``cases/`` is imported and **adjudicated**;
* every scenario is **valid and complete** (no failing and no pending check);
* no scenario has an entry in ``cases/EXCLUSIONS.json`` (an excluded scenario is replaced, never
  frozen);
* ``cases/FROZEN.json`` does not exist yet. A new version (``--supersede``) records the hash it
  replaces; a frozen set is never overwritten silently.

Writes ``cases/FROZEN.json`` — version, time, git SHA, tokenizer, ``case_set_hash`` (SHA-256 over
the sorted ``case_id:content_hash`` lines), every case's content hash, and the scenarios by split —
and sets every scenario to ``frozen`` (read-only in the Studio and for annotation). Research runs
are then accepted only for cases whose content hash is in this file (``experiments/runs.py``).
Tagging the commit (``git tag cases-v1``) is left to the team.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from gbya.cases.store import cases_dir, import_scenario, scenario_ids
from gbya.cases.validator import EXCLUSIONS, validate_scenario
from gbya.config import Settings
from gbya.errors import Conflict, Unprocessable
from gbya.llm.tokens import TokenCounter
from gbya.store.models import Case, Scenario, utcnow

FROZEN_FILE = "FROZEN.json"


class FreezeRefused(Unprocessable):
    code = "FREEZE_REFUSED"


def case_set_hash(hashes: dict[str, str]) -> str:
    lines = "\n".join(f"{cid}:{h}" for cid, h in sorted(hashes.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


def freeze(
    session: Session,
    settings: Settings,
    counter: TokenCounter,
    *,
    version: str,
    supersede: bool = False,
    git_sha: str | None = None,
) -> dict[str, Any]:
    root = cases_dir(settings)
    out = root / FROZEN_FILE
    previous = json.loads(out.read_text()) if out.is_file() else None
    if previous is not None and not supersede:
        raise Conflict(
            f"{out} exists (version {previous.get('version')}); a frozen set is never "
            "overwritten — use --supersede with a new version",
            code="ALREADY_FROZEN",
        )
    if previous is not None and previous.get("version") == version:
        raise FreezeRefused(f"version {version} is already frozen; choose a new version")
    if not counter.name.startswith("model:"):
        raise FreezeRefused(f"validation must use the model tokenizer, not {counter.name!r}")
    sids = scenario_ids(settings)
    if not sids:
        raise FreezeRefused("no scenarios under cases/")
    excluded = json.loads((root / EXCLUSIONS).read_text()) if (root / EXCLUSIONS).is_file() else []
    problems: list[str] = []
    hashes: dict[str, str] = {}
    scenarios = []
    for sid in sids:
        import_scenario(session, settings, sid)
        row = session.get(Scenario, sid)
        assert row is not None
        if row.status not in ("adjudicated", "frozen"):
            problems.append(f"{sid}: status {row.status}, not adjudicated")
        if any(e.get("scenario_id") == sid for e in excluded):
            problems.append(f"{sid}: listed in {EXCLUSIONS}")
        report = validate_scenario(
            settings, sid, counter, write_exclusions=False, window_meta={"split": row.split}
        )
        bad = [
            f"({c.check}) {c.case_id or sid}: {c.status}"
            for c in report.checks
            if c.status != "pass"
        ]
        if bad:
            problems.append(f"{sid}: validator not all-pass: {bad[:5]}")
        for case in session.query(Case).filter(Case.scenario_id == sid):
            hashes[case.id] = str(case.content_hash)
        scenarios.append({"id": sid, "split": row.split, "window_id": row.window_id})
    if problems:
        raise FreezeRefused("The case set cannot be frozen", details={"problems": problems})
    manifest = {
        "version": version,
        "frozen_at": utcnow().isoformat(),
        "git_sha": git_sha,
        "tokenizer": counter.name,
        "case_set_hash": case_set_hash(hashes),
        "content_hashes": sorted(set(hashes.values())),
        "cases": dict(sorted(hashes.items())),
        "scenarios": scenarios,
        "counts": dict(sorted(Counter(str(s["split"]) for s in scenarios).items())),
        "supersedes": previous.get("case_set_hash") if previous else None,
    }
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    for sid in sids:
        row = session.get(Scenario, sid)
        assert row is not None
        row.status = "frozen"
    session.flush()
    return manifest


def main(argv: list[str] | None = None) -> None:
    from gbya.config import get_settings
    from gbya.experiments.runner import git_sha
    from gbya.llm.tokens import default_counter
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    ap = argparse.ArgumentParser(description="Freeze the case set (T4.10, team only)")
    ap.add_argument("--version", required=True)
    ap.add_argument("--supersede", action="store_true")
    args = ap.parse_args(argv)
    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        m = freeze(
            s,
            settings,
            default_counter(),
            version=args.version,
            supersede=args.supersede,
            git_sha=git_sha(),
        )
    path = Path(cases_dir(settings)) / FROZEN_FILE
    print(f"Frozen {len(m['cases'])} cases ({m['counts']}), case_set_hash {m['case_set_hash']}")
    print(f"Next: git add {path} cases/ && git commit && git tag {args.version}")


if __name__ == "__main__":
    main()
