"""Case construction commands (plan §D.11, §D.1.1). CONSTRUCTION PATH — command line only.

    python -m gbya.cases.cli generate <scenario_id>     # make generate-variants SID=...

``generate`` builds the seven variants from ``cases/<sid>/scenario.json``, writes the case files
(keeping existing labels), patches the E3/E4/E5 databases, writes the deterministic prefixes to
``cases/<sid>/prefixes/<variant>.json`` and imports the scenario into app.db. The Scenario Studio
runs it as a short-lived subprocess; the API never imports this module.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from gbya.cases.builder import build_cases, window_facts
from gbya.cases.models import (
    Add,
    Labels,
    case_path,
    effective_context,
    load_casefile,
    load_scenario,
    scenario_path,
)
from gbya.cases.patch import patch_database
from gbya.cases.prefix import build_prefix
from gbya.cases.store import (
    case_db,
    cases_dir,
    db_path_for,
    import_scenario,
    window_db,
    write_case,
)
from gbya.config import Settings, get_settings
from gbya.llm.tokens import TokenCounter, default_counter


def prefix_path(settings: Settings, case_id: str) -> Any:
    sid, variant = case_id.split(":", 1)
    return cases_dir(settings) / sid / "prefixes" / f"{variant}.json"


def generate(settings: Settings, sid: str, counter: TokenCounter | None = None) -> dict[str, Any]:
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    counter = counter or default_counter()
    root = cases_dir(settings)
    sc = load_scenario(scenario_path(root, sid))
    wdb = settings.resolve(window_db(settings, sc.window_id))
    if not wdb.is_file():
        raise SystemExit(f"window database {wdb} is missing (make normalise)")
    facts = window_facts(wdb)
    existing: dict[str, Labels] = {}
    for path in sorted((root / sid / "cases").glob("*.json")):
        cf = load_casefile(path)
        existing[cf.variant] = cf.labels
    cases = build_cases(sc, facts, existing)
    report: dict[str, Any] = {"scenario": sid, "variants": [c.variant for c in cases],
                              "patches": {}, "prefixes": {}}  # fmt: skip
    keep = {c.variant for c in cases}
    for stale in (root / sid / "cases").glob("*.json"):
        if stale.stem not in keep:
            stale.unlink()
    for case in cases:
        write_case(settings, case)
        if case.db_patch is not None:
            out = settings.resolve(case_db(settings, case.id))
            rep = patch_database(wdb, out, case.db_patch).to_json()
            adds = [op for op in case.db_patch.ops if isinstance(op, Add)]
            if adds and rep["added"] != [facts.max_record_id + i + 1 for i in range(len(adds))]:
                raise SystemExit(f"{case.id}: added ids {rep['added']} differ from the package")
            report["patches"][case.variant] = rep
    for case in cases:
        ctx = effective_context(sc.trusted_context, sc.target_host, case.r_edit)
        db = settings.resolve(db_path_for(settings, case, sc.window_id))
        prefix = build_prefix(case, sc, db, ctx, counter)
        path = prefix_path(settings, case.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prefix.text(), encoding="utf-8")
        report["prefixes"][case.variant] = {"queries": len(prefix.queries),
                                            "retrieved": len(prefix.retrieved)}  # fmt: skip
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        import_scenario(s, settings, sid)
    assert all(case_path(root, c.id).is_file() for c in cases)
    return report


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[0] != "generate":
        raise SystemExit("usage: python -m gbya.cases.cli generate <scenario_id>")
    print(json.dumps(generate(get_settings(), argv[1]), indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
