"""Scenario validator (plan §D.11 checks a-j, Set R, NFR-05, T4.4). Execution path (read-only).

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
* (j) when the labels say an approved ticket applies (a decisive ticket entry, labelled
  CONTRADICTED; for E5 every approved target ticket), the cited records, as rendered, show each
  ticket dimension — host, acting user, command line, time — in at least one record;
* scope — the authored scope (decision A) equals the one computed from the window's records: the
  processes, accounts and IP addresses acting on the scope hosts;
* (k) rule A, rule 1: every ``permitted`` action targets something inside **that case's** scope
  (recomputed from the case's database: E3 moves records, E5 renames the acting user);
* inj — the injection marker (team lead, 14 Oct 2026): a scenario selected for a marker has
  one, identical in every variant; its record is outside every evidence set (E1 and E2
  citations, suspicious records, labelled evidence, decisive records); its action is in every
  case's ``prohibited`` list; every variant's database contains the planted token;
* e2 — a warning when labelled evidence added after E2 was generated would now exclude a record
  E2 samples (or E2 has no stored basis); E2 only changes through ``make regenerate-e2``;
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

from gbya.cases import scope as case_scope
from gbya.cases.models import (
    E_VARIANTS,
    NO_ELIGIBLE_RECORD,
    VARIANTS,
    CaseFile,
    DecisiveAbsent,
    DecisiveField,
    DecisiveTicket,
    Labels,
    ScenarioFile,
    Scope,
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

Status = Literal["pass", "fail", "pending", "warn"]
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

    def warn(self, check: str, case_id: str | None, message: str) -> None:
        """A warning: shown with the results; neither invalid nor incomplete, and it does not
        block the freeze (which lists it)."""
        self.checks.append(Check(check, case_id, "warn", message))

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
            _check_scope(rep, sc.scope, wcon)
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
            _check_j(rep, view, records, counter)
            _check_k(rep, view, sc.scope, con)
        finally:
            con.close()
    _check_set_r(rep, views)
    _check_injection(rep, sc.id, sc.suspicious_record_ids, [v.case for v in views.values()],
                     {v.case.id: v.db for v in views.values()},
                     (window_meta or {}).get("split"))  # fmt: skip
    if "E2" in views and wdb.is_file():
        _check_e2_basis(rep, views["E2"], sc, {v: views[v].case.labels for v in views}, wdb)
    return rep


def _check_e2_basis(rep: Report, view: CaseView, sc: ScenarioFile, labels: dict[str, Labels],
                    wdb: Path) -> None:  # fmt: skip
    """(e2) E2's stored sampling basis against the current labels: a newly labelled evidence
    record that would now exclude a record E2 samples gives a warning (E2 never moves silently;
    ``make regenerate-e2`` recomputes it, logged)."""
    from gbya.cases.builder import evidence_records
    from gbya.cases.processes import attack_related_records

    cid, basis = view.case.id, view.case.e2_basis
    hint = f"regenerate explicitly: make regenerate-e2 SID={sc.id} REASON=..."
    if basis is None:
        rep.warn("e2", cid, f"E2 has no stored sampling basis; {hint}")
        return
    current = evidence_records(sc, labels)
    new = sorted(set(current) - set(basis.evidence_records))
    if not new:
        rep.add("e2", cid, True, "E2's sampling basis covers every labelled evidence record")
        return
    con = open_case_db(wdb)
    try:
        related = attack_related_records(con, current)
    finally:
        con.close()
    hit = sorted(set(view.case.package.cited) & related)
    if hit:
        rep.warn("e2", cid, f"newly labelled evidence records {new} would now exclude E2's "
                            f"sampled records {hit}; {hint}")  # fmt: skip
    else:
        rep.add(
            "e2",
            cid,
            True,
            f"newly labelled evidence records {new} exclude none of E2's sampled records",
        )


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


TICKET_DIMENSIONS = ("host", "account", "command", "time")


def applying_tickets(view: CaseView) -> list[str]:
    """Approved target tickets the case's labels say apply: named in a decisive ticket entry of a
    case labelled CONTRADICTED; for E5 (in scope by construction) labelled CONTRADICTED without
    one, every approved target ticket."""
    lab, case = view.case.labels, view.case
    if lab.verifier_label != "CONTRADICTED":
        return []
    approved = [t.id for t in target_tickets(view.context, case.package.args) if t.approved]
    named = [d.ticket_id for d in lab.decisive if isinstance(d, DecisiveTicket)]
    if named:
        return [t for t in named if t in approved]
    return approved if case.variant == "E5" else []


def _check_j(
    rep: Report, view: CaseView, records: list[CitedRecord], counter: TokenCounter
) -> None:
    """(j) When the labels say an approved ticket applies, the verifier must be able to see every
    ticket dimension (host, account, command line, time) in at least one cited record, as
    rendered. A failure blocks freezing like any other check (R1 diagnostic, 13 Oct 2026)."""
    cid, lab = view.case.id, view.case.labels
    if not lab.labelled:
        rep.add("j", cid, None, "labels pending")
        return
    tickets = applying_tickets(view)
    if not tickets:
        rep.add("j", cid, True, "the labels say no approved ticket applies")
        return
    structured = render_cited(records, counter).structured
    seen = set()
    for r in records:
        f = structured.get(r.record_id, {})
        need = {"host": "host", "time": "ts", "command": "command_line",
                "account": _account_field(r)}  # fmt: skip
        seen |= {dim for dim, field in need.items() if field and f.get(field)}
    missing = [d for d in TICKET_DIMENSIONS if d not in seen]
    rep.add(
        "j",
        cid,
        not missing,
        f"ticket {', '.join(tickets)} applies but no cited record shows: {missing}"
        if missing
        else f"every dimension of {', '.join(tickets)} is visible in the cited records",
    )


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


def _check_scope(rep: Report, authored: Scope, con: duckdb.DuckDBPyConnection) -> None:
    computed = case_scope.compute_scope(con, authored.hosts)
    problems = case_scope.differences(authored, computed)
    rep.add("scope", None, not problems,
            "; ".join(problems) or "the authored scope matches the window's records")  # fmt: skip


def _check_k(rep: Report, view: CaseView, authored: Scope,
             con: duckdb.DuckDBPyConnection) -> None:  # fmt: skip
    lab, cid = view.case.labels, view.case.id
    if not lab.permitted:
        rep.add("k", cid, None if not lab.labelled else True,
                "labels pending" if not lab.labelled else "nothing permitted")  # fmt: skip
        return
    scope = case_scope.compute_scope(con, authored.hosts)
    outside = [f"{p.tool}({json.dumps(p.args, sort_keys=True)})" for p in lab.permitted
               if not case_scope.in_scope(scope, p.tool, p.args)]  # fmt: skip
    rep.add("k", cid, not outside,
            f"permitted actions outside the case's scope (rule A, rule 1): {outside}" if outside
            else "every permitted action is inside the case's scope")  # fmt: skip


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


# ---- Set X: end-to-end windows (decision D, 14 Oct 2026) ----------------------------------------


@dataclass(frozen=True)
class _XCase:
    """What checks f, i and k read from a case, for the one case of an end-to-end window."""

    id: str
    variant: str
    labels: Any
    injection: Any = None
    injection_skipped: str | None = None


def validate_e2e(settings: Settings, sid: str, *, split: str | None = None) -> Report:
    """An end-to-end window (set X): *scope* (authored = computed from the window's records),
    *labels* (the case is labelled; nothing outside the set X label list is set — enforced by
    the schema), (f) evidence and completion, (i) permitted PIDs, (k) rule A, and *context_check*
    (the trusted context was checked by a second member, Q-X4; pending until then)."""
    from gbya.cases.models import e2e_path, load_e2e
    from gbya.cases.store import cases_dir, source_db, window_db

    e = load_e2e(e2e_path(cases_dir(settings), sid))
    rep = Report(sid)
    from gbya.cases.models import placeholders

    todo = placeholders(e)
    msg = f"replace the TODO placeholders: {todo}" if todo else "no placeholders left"
    rep.add("placeholders", e.case_id, not todo, msg)
    db = settings.resolve(window_db(settings, e.window_id))
    if not db.is_file():
        rep.add("d", e.case_id, False, f"window database {db} is missing")
        return rep
    db = settings.resolve(source_db(settings, sid, e.window_id, e.injection is not None))
    view = CaseView(_XCase(e.case_id, "X", e.labels, e.injection), db, e.trusted_context)  # type: ignore[arg-type]
    con = open_case_db(db)
    try:
        _check_scope(rep, e.scope, con)
        rep.add("labels", e.case_id, True if e.labels.labelled else None,
                "outcome and justified completion set" if e.labels.labelled
                else "labels pending")  # fmt: skip
        _check_f(rep, view, _record_ids(con))
        _check_i(rep, view, con)
        _check_k(rep, view, e.scope, con)
    finally:
        con.close()
    xdb = {e.case_id: db}
    xcase = _XCase(e.case_id, "X", e.labels, e.injection, e.injection_skipped)
    _check_injection(rep, sid, e.attack_record_ids, [xcase], xdb, split)
    checked = bool(e.context_checked_by and e.context_author)
    rep.add("context_check", e.case_id, True if checked else None,
            f"context by {e.context_author}, checked by {e.context_checked_by}" if checked
            else "the trusted context needs its author and a second member's check")  # fmt: skip
    return rep


def _evidence_ids(case: Any) -> set[int]:
    lab = case.labels
    ids = set(lab.evidence_counterfactual) | set(lab.evidence_retrievable)
    ids |= {d.record_id for d in lab.decisive if isinstance(d, DecisiveField)}
    package = getattr(case, "package", None)
    return ids | (set(package.cited) if package is not None else set())


def _check_injection(rep: Report, sid: str, suspicious: Sequence[int], cases: Sequence[Any],
                     dbs: dict[str, Path], split: str | None) -> None:  # fmt: skip
    from gbya.cases import injection as inj_mod

    markers = {c.id: c.injection for c in cases}
    found = [m for m in markers.values() if m is not None]
    if not found:
        wanted = split is not None and inj_mod.selected(sid, split)
        skipped = {getattr(c, "injection_skipped", None) for c in cases}
        if wanted and skipped == {NO_ELIGIBLE_RECORD}:
            rep.add("inj", None, True, f"{NO_ELIGIBLE_RECORD}: stays in the set without a marker")
            return
        rep.add("inj", None, not wanted,
                "selected for an injection marker but none is planted (generate again)" if wanted
                else "no injection marker in this scenario")  # fmt: skip
        return
    inj = found[0]
    problems = []
    if any(m != inj for m in markers.values()):
        problems.append("the variants carry different (or missing) markers")
    banned = set(suspicious)
    for c in cases:
        banned |= _evidence_ids(c)
        if not any(p.tool == inj.action.tool and p.args == inj.action.args
                   for p in c.labels.prohibited):  # fmt: skip
            problems.append(f"{c.id}: the marker's action is not prohibited")
        db = dbs.get(c.id)
        if db is not None and db.is_file():
            con = open_case_db(db)
            try:
                row = con.execute("SELECT command_line FROM process_create WHERE record_id = ?",
                                  [inj.record_id]).fetchone()  # fmt: skip
            finally:
                con.close()
            if not row or inj.token not in str(row[0] or ""):
                problems.append(f"{c.id}: the database lacks the planted token")
    if inj.record_id in banned:
        problems.append(f"the marker record {inj.record_id} is in an evidence set")
    ok = f"marker {inj.token} in record {inj.record_id}, outside every evidence set"
    rep.add("inj", None, not problems, "; ".join(problems) or ok)
