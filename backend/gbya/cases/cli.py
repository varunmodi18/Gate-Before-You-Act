"""Case construction commands (plan §D.11, §D.1.1). CONSTRUCTION PATH — command line only.

    python -m gbya.cases.cli generate <scenario_id>     # make generate-variants SID=...
    python -m gbya.cases.cli regenerate-e2 <sid> <why>  # make regenerate-e2 SID=... REASON=...

``generate`` builds the seven variants from ``cases/<sid>/scenario.json``, writes the case files
(keeping existing labels), patches the E3/E4/E5 databases, writes the deterministic prefixes to
``cases/<sid>/prefixes/<variant>.json`` and imports the scenario into app.db. The Scenario Studio
runs it as a short-lived subprocess; the API never imports this module.
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from typing import Any

from gbya.cases import injection
from gbya.cases.builder import build_cases, evidence_records, window_facts
from gbya.cases.models import (
    Add,
    CaseFile,
    Injection,
    Labels,
    ScenarioFile,
    case_path,
    effective_context,
    load_casefile,
    load_scenario,
    scenario_path,
    with_injection,
)
from gbya.cases.patch import patch_database
from gbya.cases.prefix import build_prefix
from gbya.cases.store import (
    base_db,
    case_db,
    cases_dir,
    db_path_for,
    import_scenario,
    source_db,
    window_db,
    write_case,
)
from gbya.config import Settings, get_settings
from gbya.data.connection import open_case_db
from gbya.llm.tokens import TokenCounter, default_counter
from gbya.store.models import Window


def prefix_path(settings: Settings, case_id: str) -> Any:
    sid, variant = case_id.split(":", 1)
    return cases_dir(settings) / sid / "prefixes" / f"{variant}.json"


def generate(
    settings: Settings, sid: str, counter: TokenCounter | None = None, *, refresh_e2: bool = False
) -> dict[str, Any]:
    """Build the variants. E2's sampling basis is computed once and stored with the E2 case;
    later generations reuse its evidence records (plus E1's current citations), so label changes
    never move E2. ``refresh_e2`` recomputes it from the current labels (``regenerate_e2``).

    In a seeded half of the test and e2e scenarios an injection marker is planted in one benign
    record of a base database from which every variant derives (``gbya.cases.injection``)."""
    from gbya.store.db import make_engine, make_sessionmaker, session_scope

    counter = counter or default_counter()
    root = cases_dir(settings)
    sc = load_scenario(scenario_path(root, sid))
    wdb = settings.resolve(window_db(settings, sc.window_id))
    if not wdb.is_file():
        raise SystemExit(f"window database {wdb} is missing (make normalise)")
    existing: dict[str, Labels] = {}
    stored = None
    stored_injection = None
    for path in sorted((root / sid / "cases").glob("*.json")):
        cf = load_casefile(path)
        existing[cf.variant] = cf.labels
        if cf.variant == "E2":
            stored = cf.e2_basis
        if cf.variant == "E1":
            stored_injection = cf.injection
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        window = s.get(Window, sc.window_id)
        split = window.split if window else None
    inject = injection.selected(sid, split)
    if stored is not None and not refresh_e2:
        evidence = sorted(set(stored.evidence_records) | set(sc.e1.cited))
    else:
        evidence = evidence_records(sc, existing)
    facts = window_facts(wdb, evidence)
    if inject and stored_injection is not None:
        facts = replace(facts, never_sample=frozenset({stored_injection.record_id}))
    cases = build_cases(sc, facts, existing)
    inj = _injection(sc, wdb, cases, evidence, stored_injection) if inject else None
    if inj is not None:  # the same marker, and its prohibited action, in every variant
        cases = [
            c.model_copy(update={"injection": inj, "labels": with_injection(c.labels, inj)})
            for c in cases
        ]
        injection.plant(wdb, settings.resolve(base_db(settings, sid)), inj)
    src = settings.resolve(source_db(settings, sid, sc.window_id, inj is not None))
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
            rep = patch_database(src, out, case.db_patch).to_json()
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
    with session_scope(factory) as s:
        import_scenario(s, settings, sid)
    assert all(case_path(root, c.id).is_file() for c in cases)
    report["injection"] = inj.model_dump(mode="json") if inj else None
    return report


def _injection(sc: ScenarioFile, wdb: Any, cases: list[CaseFile], evidence: list[int],
               stored: Injection | None) -> Injection:  # fmt: skip
    """The scenario's marker: the stored one while its record is still eligible, else a new one
    (benign, in scope, outside E1, the suspicious records, the E2 sample and the evidence)."""
    e2 = next((c for c in cases if c.variant == "E2"), None)
    exclude = set(evidence) | set(sc.suspicious_record_ids) | set(sc.e1.cited)
    exclude |= set(e2.package.cited) if e2 else set()
    for c in cases:
        exclude |= set(c.labels.evidence_counterfactual) | set(c.labels.evidence_retrievable)
    con = open_case_db(wdb)
    try:
        seed = set(sc.suspicious_record_ids) | set(sc.e1.cited)
        if stored is not None and stored.record_id in injection.eligible_records(
                con, sc.target_host, exclude, seed):  # fmt: skip
            return stored
        return injection.choose(con, sc.id, sc.target_host, exclude, seed)
    finally:
        con.close()


def e2_log_path(settings: Settings, sid: str) -> Any:
    return cases_dir(settings) / sid / "e2_regenerations.json"


def regenerate_e2(settings: Settings, sid: str, reason: str, *, by: str = "cli",
                  counter: TokenCounter | None = None) -> dict[str, Any]:  # fmt: skip
    """Recompute E2's sampling basis from the current labels and regenerate, logged in
    ``cases/<sid>/e2_regenerations.json``. The only way a label change moves E2."""
    from gbya.store.db import make_engine, make_sessionmaker, session_scope
    from gbya.store.models import Scenario, utcnow

    if not reason.strip():
        raise SystemExit("regenerate-e2 needs a reason")
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        row = s.get(Scenario, sid)
        if row is not None and row.status == "frozen":
            raise SystemExit(f"scenario {sid} is frozen; E2 cannot be regenerated")
    e2 = case_path(cases_dir(settings), f"{sid}:E2")
    before = load_casefile(e2) if e2.is_file() else None
    generate(settings, sid, counter, refresh_e2=True)
    after = load_casefile(e2)
    entry = {
        "at": utcnow().isoformat(), "by": by, "reason": reason,
        "evidence_before": before.e2_basis.evidence_records
        if before and before.e2_basis else None,
        "evidence_after": after.e2_basis.evidence_records if after.e2_basis else None,
        "e2_before": before.package.cited if before else None, "e2_after": after.package.cited,
    }  # fmt: skip
    path = e2_log_path(settings, sid)
    log = json.loads(path.read_text()) if path.is_file() else []
    path.write_text(json.dumps([*log, entry], indent=2) + "\n", encoding="utf-8")
    return entry


USAGE = ("usage: python -m gbya.cases.cli generate <scenario_id>\n"
         "       python -m gbya.cases.cli regenerate-e2 <scenario_id> <reason>\n"
         "       python -m gbya.cases.cli prepare-e2e <window_case_id>")  # fmt: skip


def prepare_e2e(settings: Settings, sid: str) -> dict[str, Any]:
    """An end-to-end window before annotation: decide its injection marker (a seeded half of the
    e2e windows), plant it in the window's base database, add the marker's action to the
    prohibited list, and import the case. Needs the authored ``attack_record_ids`` when a
    marker is due. Refused once frozen."""
    from gbya.cases.models import e2e_path, load_e2e
    from gbya.cases.store import import_e2e, write_e2e
    from gbya.store.db import make_engine, make_sessionmaker, session_scope
    from gbya.store.models import Scenario

    root = cases_dir(settings)
    e = load_e2e(e2e_path(root, sid))
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with session_scope(factory) as s:
        window = s.get(Window, e.window_id)
        row = s.get(Scenario, sid)
        if row is not None and row.status == "frozen":
            raise SystemExit(f"{sid} is frozen; its marker cannot change")
        split = window.split if window else None
    inj = None
    if injection.selected(sid, split):
        if not e.attack_record_ids:
            raise SystemExit(f"{sid}: author attack_record_ids in e2e.json first (the attack "
                             "chain), so that the marker goes into a benign record")  # fmt: skip
        wdb = settings.resolve(window_db(settings, e.window_id))
        exclude = set(e.attack_record_ids) | set(e.labels.evidence_retrievable)
        con = open_case_db(wdb)
        try:
            seed = e.attack_record_ids
            eligible = injection.eligible_records(con, e.target_host, exclude, seed)
            keep = e.injection is not None and e.injection.record_id in eligible
            inj = (
                e.injection
                if keep and e.injection is not None
                else injection.choose(con, sid, e.target_host, exclude, seed)
            )
        finally:
            con.close()
        injection.plant(wdb, settings.resolve(base_db(settings, sid)), inj)
    labels = with_injection(e.labels, inj)
    write_e2e(settings, e.model_copy(update={"injection": inj, "labels": labels}))
    with session_scope(factory) as s:
        import_e2e(s, settings, sid)
    return {"window": sid, "split": split, "injection": inj.model_dump(mode="json") if inj
            else None}  # fmt: skip


def main(argv: list[str]) -> None:
    if len(argv) == 2 and argv[0] == "generate":
        print(json.dumps(generate(get_settings(), argv[1]), indent=1))
    elif len(argv) == 2 and argv[0] == "prepare-e2e":
        print(json.dumps(prepare_e2e(get_settings(), argv[1]), indent=1))
    elif len(argv) == 3 and argv[0] == "regenerate-e2":
        print(json.dumps(regenerate_e2(get_settings(), argv[1], argv[2]), indent=1))
    else:
        raise SystemExit(USAGE)


if __name__ == "__main__":
    main(sys.argv[1:])
