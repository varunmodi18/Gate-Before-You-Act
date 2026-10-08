"""Scenario validator (plan §D.11 checks a-i, Set R, NFR-05, T4.4). Execution path (read-only).

Per scenario, every check gives ``pass``, ``fail`` or ``pending`` (a label check on a case not yet
labelled). A scenario is valid when nothing fails; it is complete when nothing is pending either.

* variants — all seven cases exist;
* (a) the effective trusted-context hash is identical across E1-E5;
* (b) E1-E4: no suspicious event lies inside an approved target ticket in all four fields;
  E5: every suspicious event does (one approved ticket covers them all). The command of an event
  without a command line is its acting process's (PID lineage on the same host);
* (c) E3: no suspicious event is left on the target host;
* (d) every cited ID exists in the case's database;
* (e) request, ``technique_claimed`` and ``rationale`` are identical across E1-E5;
* (f) ``evidence_retrievable`` ⊆ the case's records; ``has_justified_completion`` agrees with the
  permitted list and the approval script;
* (g) the package fits the evidence budget of §D.7.1 rendered in full (8 records, 3,200 tokens);
  failures are logged in ``cases/EXCLUSIONS.json``;
* (h) every ``decisive`` entry holds in the rendered standard-variant verifier prompt, for that
  record and that field; an E4 contradiction record is cited;
* (i) a permitted ``kill_process`` PID is the acting process of a record in
  ``evidence_retrievable``;
* set_r — R_pos and R_neg differ in exactly one field (tier, approval script or toolset) and
  otherwise equal E1.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import duckdb

from gbya.cases.models import (
    E_VARIANTS,
    VARIANTS,
    CaseFile,
    DecisiveAbsent,
    DecisiveField,
    DecisiveTicket,
    ScenarioFile,
    effective_context,
    effective_toolset,
    load_casefile,
    load_scenario,
    scenario_path,
)
from gbya.config import Settings
from gbya.context.models import ChangeTicket, TrustedContext, host_key
from gbya.context.store import context_hash
from gbya.data.connection import open_case_db
from gbya.gate.checks import ACTOR_PID
from gbya.gate.evidence import (
    MAX_CITED,
    MAX_EVIDENCE_TOKENS,
    CitedRecord,
    read_cited,
    render_cited,
)
from gbya.gate.ticket_scope import FIELDS, record_matches
from gbya.gate.types import Claim
from gbya.gate.verifier import build_prompt, target_tickets
from gbya.llm.tokens import TokenCounter

Status = Literal["pass", "fail", "pending"]
EXCLUSIONS = "EXCLUSIONS.json"


@dataclass(frozen=True)
class Check:
    check: str
    case_id: str | None
    status: Status
    message: str


@dataclass
class Report:
    scenario_id: str
    checks: list[Check] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    @property
    def complete(self) -> bool:
        return self.valid and not any(c.status == "pending" for c in self.checks)

    def add(self, check: str, case_id: str | None, ok: bool | None, message: str) -> None:
        status: Status = "pending" if ok is None else ("pass" if ok else "fail")
        self.checks.append(Check(check, case_id, status, message))

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "valid": self.valid,
            "complete": self.complete,
            "checks": [asdict(c) for c in self.checks],
        }


# ---- helpers -------------------------------------------------------------------------------------


def _record_ids(con: duckdb.DuckDBPyConnection) -> set[int]:
    return {int(r[0]) for r in con.execute("SELECT record_id FROM raw_events").fetchall()}


def _command_of(con: duckdb.DuckDBPyConnection, rec: CitedRecord) -> str | None:
    """The record's command line, or its acting process's (latest process_create of that PID on
    the same host at or before the record)."""
    if rec.table is None:
        return None
    own = rec.values.get("command_line")
    if own:
        return str(own)
    pid = next((rec.values.get(c) for c in ACTOR_PID.get(rec.table, ()) if rec.values.get(c)), None)
    if pid is None or rec.ts is None or rec.host is None:
        return None
    row = con.execute(
        "SELECT command_line FROM process_create WHERE pid = ? AND lower(host) = ? AND ts <= ? "
        "AND command_line IS NOT NULL ORDER BY ts DESC, record_id DESC LIMIT 1",
        [int(pid), host_key(rec.host), rec.ts],
    ).fetchone()
    return str(row[0]) if row else None


def _in_scope(con: duckdb.DuckDBPyConnection, rec: CitedRecord, t: ChangeTicket) -> bool:
    return all(record_matches(rec, t, _command_of(con, rec)).values())


@dataclass
class CaseView:
    case: CaseFile
    db: Path
    context: TrustedContext


def _views(settings: Settings, sc: ScenarioFile, cases: Sequence[CaseFile]) -> dict[str, CaseView]:
    from gbya.cases.store import db_path_for

    return {
        c.variant: CaseView(
            c,
            settings.resolve(db_path_for(settings, c, sc.window_id)),
            effective_context(sc.trusted_context, sc.target_host, c.r_edit),
        )
        for c in cases
    }


# ---- the checks ----------------------------------------------------------------------------------


def validate_scenario(
    settings: Settings,
    sid: str,
    counter: TokenCounter,
    *,
    window_meta: dict[str, Any] | None = None,
    write_exclusions: bool = True,
) -> Report:
    root = settings.resolve(settings.cases_dir)
    sc = load_scenario(scenario_path(root, sid))
    cases = [load_casefile(p) for p in sorted((root / sid / "cases").glob("*.json"))]
    rep = Report(sid)
    views = _views(settings, sc, cases)
    missing = [v for v in VARIANTS if v not in views]
    rep.add(
        "variants",
        None,
        not missing,
        f"missing variants: {missing}" if missing else "all seven cases exist",
    )
    e_views = [views[v] for v in E_VARIANTS if v in views]

    hashes = {v.case.variant: context_hash(v.context) for v in e_views}
    rep.add(
        "a",
        None,
        len(set(hashes.values())) <= 1,
        "context hash identical across E1-E5"
        if len(set(hashes.values())) <= 1
        else f"context hashes differ: {hashes}",
    )

    claims = {
        (
            json.dumps(v.case.request.model_dump(), sort_keys=True),
            v.case.package.technique_claimed,
            v.case.package.rationale,
        )
        for v in e_views
    }
    rep.add(
        "e",
        None,
        len(claims) <= 1,
        "request, technique_claimed and rationale identical across E1-E5"
        if len(claims) <= 1
        else "request, technique_claimed or rationale differ",
    )

    from gbya.cases.store import window_db

    wdb = settings.resolve(window_db(settings, sc.window_id))
    window_max: int | None = None
    if wdb.is_file():
        wcon = open_case_db(wdb)
        try:
            window_max = max(_record_ids(wcon), default=0)
        finally:
            wcon.close()
    for view in views.values():
        cid, case = view.case.id, view.case
        if not view.db.is_file():
            rep.add("d", cid, False, f"case database {view.db} is missing (generate variants)")
            continue
        con = open_case_db(view.db)
        try:
            ids = _record_ids(con)
            by_id = read_cited(con, case.package.cited)
            records = [by_id[i] for i in dict.fromkeys(case.package.cited) if i in by_id]

            missing_ids = [i for i in case.package.cited if i not in ids]
            rep.add(
                "d",
                cid,
                not missing_ids,
                f"cited IDs missing: {missing_ids}" if missing_ids else "every cited ID exists",
            )

            if case.variant in E_VARIANTS:
                _check_b(rep, con, sc, view, ids)
            if case.variant == "E3":
                left = _on_target(con, sc, ids)
                rep.add(
                    "c",
                    cid,
                    not left,
                    f"suspicious events still on the target: {left}"
                    if left
                    else "no suspicious event on the target host",
                )

            _check_g(rep, view, records, counter, sc, window_meta, root, write_exclusions)
            _check_f(rep, view, ids)
            _check_h(rep, view, sc, by_id, records, ids, counter, window_max)
            _check_i(rep, view, con)
        finally:
            con.close()
    _check_set_r(rep, views)
    return rep


def _target_approved(sc: ScenarioFile, ctx: TrustedContext) -> list[ChangeTicket]:
    args = {"host": sc.target_host, **sc.request.target}
    return [t for t in target_tickets(ctx, args) if t.approved]


def _suspicious(
    con: duckdb.DuckDBPyConnection, sc: ScenarioFile, ids: set[int]
) -> list[CitedRecord]:
    present = [i for i in sc.suspicious_record_ids if i in ids]
    by_id = read_cited(con, present)
    return [by_id[i] for i in present if i in by_id]


def _on_target(con: duckdb.DuckDBPyConnection, sc: ScenarioFile, ids: set[int]) -> list[int]:
    return [
        r.record_id
        for r in _suspicious(con, sc, ids)
        if r.host and host_key(r.host) == host_key(sc.target_host)
    ]


def _check_b(
    rep: Report, con: duckdb.DuckDBPyConnection, sc: ScenarioFile, view: CaseView, ids: set[int]
) -> None:
    cid, variant = view.case.id, view.case.variant
    events = _suspicious(con, sc, ids)
    tickets = _target_approved(sc, view.context)
    if variant == "E5":
        if not tickets:
            rep.add("b", cid, False, "E5 needs an approved ticket for the target")
            return
        covering = [t.id for t in tickets if events and all(_in_scope(con, e, t) for e in events)]
        detail = {e.record_id: record_matches(e, tickets[0], _command_of(con, e)) for e in events}
        outside = {
            k: [f for f in FIELDS if not m[f]] for k, m in detail.items() if not all(m.values())
        }
        rep.add(
            "b",
            cid,
            bool(covering),
            f"every suspicious event inside ticket {covering[0]}"
            if covering
            else f"events outside the ticket ({tickets[0].id}): {outside}",
        )
        return
    inside = [(e.record_id, t.id) for e in events for t in tickets if _in_scope(con, e, t)]
    rep.add(
        "b",
        cid,
        not inside,
        f"suspicious events fully inside an approved ticket: {inside}"
        if inside
        else "every suspicious event differs from every approved ticket in ≥ 1 field",
    )


def _check_g(
    rep: Report,
    view: CaseView,
    records: list[CitedRecord],
    counter: TokenCounter,
    sc: ScenarioFile,
    window_meta: dict[str, Any] | None,
    root: Path,
    write: bool,
) -> None:
    case = view.case
    n = len(case.package.cited)
    tokens = render_cited(records, counter).tokens
    reason = None
    if n > MAX_CITED:
        reason = {"reason": "record_count", "measured": n, "limit": MAX_CITED}
    elif tokens > MAX_EVIDENCE_TOKENS:
        reason = {"reason": "rendered_tokens", "measured": tokens, "limit": MAX_EVIDENCE_TOKENS}
    rep.add(
        "g",
        case.id,
        reason is None,
        f"{n} records, {tokens} tokens ({counter.name}) within budget"
        if reason is None
        else f"over the evidence budget: {reason}",
    )
    if write:
        _log_exclusion(root, sc, case.id, reason, window_meta or {})


def _log_exclusion(
    root: Path, sc: ScenarioFile, case_id: str, reason: dict[str, Any] | None, meta: dict[str, Any]
) -> None:
    path = root / EXCLUSIONS
    entries: list[dict[str, Any]] = json.loads(path.read_text()) if path.is_file() else []
    kept = [e for e in entries if e.get("case_id") != case_id]
    if reason is not None:
        kept.append(
            {
                "scenario_id": sc.id,
                "case_id": case_id,
                "window_id": sc.window_id,
                "techniques": meta.get("techniques", []),
                "tactics": meta.get("tactics", []),
                "split": meta.get("split"),
                **reason,
            }
        )
    if kept != entries:
        kept.sort(key=lambda e: e["case_id"])
        path.write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n")


def _check_f(rep: Report, view: CaseView, ids: set[int]) -> None:
    lab, cid = view.case.labels, view.case.id
    if not lab.labelled:
        rep.add("f", cid, None, "labels pending")
        return
    outside = [i for i in lab.evidence_retrievable if i not in ids]
    mode = view.context.approval_script.mode
    reachable = any(
        p.fulfils and (not p.requires_approval or mode == "grant") for p in lab.permitted
    )
    problems = []
    if outside:
        problems.append(f"evidence_retrievable not in the case database: {outside}")
    if lab.has_justified_completion is None or lab.has_justified_completion != reachable:
        problems.append(
            f"has_justified_completion={lab.has_justified_completion} but the permitted "
            f"list and approval script ({mode}) give {reachable}"
        )
    rep.add("f", cid, not problems, "; ".join(problems) or "evidence and completion consistent")


def _check_h(
    rep: Report,
    view: CaseView,
    sc: ScenarioFile,
    by_id: dict[int, CitedRecord],
    records: list[CitedRecord],
    ids: set[int],
    counter: TokenCounter,
    window_max: int | None,
) -> None:
    case, cid, lab = view.case, view.case.id, view.case.labels
    if case.variant == "E4" and lab.e4_kind == "contradiction":
        added = [i for i in case.package.cited if i > (window_max or max(ids))]
        rep.add(
            "h",
            cid,
            bool(added) and all(i in ids for i in added),
            "the added contradicting record is cited"
            if added
            else "E4 contradiction: the added record is not cited",
        )
    if not lab.labelled:
        rep.add("h", cid, None, "labels pending")
        return
    if not lab.decisive:
        rep.add("h", cid, False, "no decisive entries")
        return
    problems = decisive_problems(view, records, ids, list(lab.decisive), counter)
    rep.add(
        "h", cid, not problems, "; ".join(problems) or "every decisive entry holds in the prompt"
    )


def decisive_problems(
    view: CaseView,
    records: list[CitedRecord],
    ids: set[int],
    entries: Sequence[Any],
    counter: TokenCounter,
) -> list[str]:
    """Why decisive entries do not hold in the rendered standard-variant prompt (empty: they all
    hold). Used by check (h) and by the Annotate page's live check."""
    case = view.case
    prompt = standard_prompt(view, records, counter)
    structured = render_cited(records, counter).structured
    tickets = {t.id for t in target_tickets(view.context, case.package.args)}
    problems = []
    for d in entries:
        if isinstance(d, DecisiveField):
            value = structured.get(d.record_id, {}).get(d.field)
            if d.record_id not in structured:
                problems.append(f"record {d.record_id} is not cited")
            elif value is None or d.contains not in str(value):
                problems.append(f"{d.contains!r} not in record {d.record_id} field {d.field}")
        elif isinstance(d, DecisiveTicket):
            if d.ticket_id not in tickets or d.ticket_id not in prompt.blocks["CHANGE_TICKETS"]:
                problems.append(f"ticket {d.ticket_id} not in CHANGE_TICKETS")
            for r in records:
                f = structured.get(r.record_id, {})
                need = {
                    "host": "host",
                    "time": "ts",
                    "command": "command_line",
                    "account": _account_field(r),
                }
                gaps = [s for s in d.scope if not f.get(need[s] or "")]
                if gaps:
                    problems.append(f"record {r.record_id} lacks scope fields {gaps}")
        elif isinstance(d, DecisiveAbsent):
            if d.absent_record_id in case.package.cited or d.absent_record_id in ids:
                problems.append(f"record {d.absent_record_id} is cited or still in the database")
    return problems


def _account_field(rec: CitedRecord) -> str | None:
    from gbya.data.fieldmap import acting_user_column

    if rec.table is None:
        return None
    return acting_user_column(rec.table, int(rec.values.get("event_id") or 0))


def standard_prompt(
    view: CaseView, records: list[CitedRecord], counter: TokenCounter, reference: Sequence[Any] = ()
) -> Any:
    """The standard-variant verifier prompt for a case (reference documents optional: the
    decisive checks concern the records and tickets only)."""
    from gbya.config import REPO_ROOT
    from gbya.policy.engine import load_evidence_requirements

    reqs = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
    case = view.case
    args = {**case.package.args, "cited": list(case.package.cited)}
    return build_prompt(
        variant="standard",
        tool=case.package.tool,
        args=args,
        claim=Claim(
            technique_claimed=case.package.technique_claimed, rationale=case.package.rationale
        ),
        requirement=reqs.get(case.package.tool, ""),
        evidence=render_cited(records, counter),
        reference=list(reference),
        tickets=target_tickets(view.context, args),
        counter=counter,
    )


def _check_i(rep: Report, view: CaseView, con: duckdb.DuckDBPyConnection) -> None:
    lab, cid = view.case.labels, view.case.id
    kills = [p for p in lab.permitted if p.tool == "kill_process"]
    if not lab.labelled:
        rep.add("i", cid, None, "labels pending")
        return
    if not kills:
        rep.add("i", cid, True, "no permitted kill_process")
        return
    recs = read_cited(con, lab.evidence_retrievable)
    problems = []
    for p in kills:
        pid = int(p.args.get("pid", -1))
        actor = [
            r.record_id
            for r in recs.values()
            if r.table and any(r.values.get(c) == pid for c in ACTOR_PID.get(r.table, ()))
        ]
        if not actor:
            problems.append(
                f"PID {pid} is not the acting process of any evidence_retrievable record"
            )
    rep.add("i", cid, not problems, "; ".join(problems) or "permitted PIDs are acting processes")


def _check_set_r(rep: Report, views: dict[str, CaseView]) -> None:
    if "R_pos" not in views or "R_neg" not in views:
        return
    pos, neg = views["R_pos"], views["R_neg"]
    target = pos.case.request.target.get("host")

    def tier(v: CaseView) -> int | None:
        a = v.context.asset(str(target)) if target else None
        return a.tier if a else None

    diffs = {
        "tier": tier(pos) != tier(neg),
        "approval_script": pos.context.approval_script != neg.context.approval_script,
        "toolset": effective_toolset(pos.case.r_edit) != effective_toolset(neg.case.r_edit),
    }
    changed = [k for k, v in diffs.items() if v]
    others_equal = (
        pos.case.request == neg.case.request
        and pos.case.package == neg.case.package
        and pos.db == neg.db
        and pos.case.db_patch is None
        and neg.case.db_patch is None
    )
    if "E1" in views:
        others_equal = others_equal and pos.case.package == views["E1"].case.package
    ok = len(changed) == 1 and others_equal
    rep.add(
        "set_r",
        None,
        ok,
        f"R_pos and R_neg differ only in {changed[0]}"
        if ok
        else f"R_pos/R_neg must differ in exactly one field (changed: {changed}) and otherwise "
        "equal E1",
    )
