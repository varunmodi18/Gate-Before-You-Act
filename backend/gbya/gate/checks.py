"""Gate checks (plan §D.6.2): C1 (T2.4), C2, C3 and C5 (T2.5). C4 and C6 run in ``gate.py``.

C1, in this order, first failure decides:

1. the tool is on the configuration's allow-list (``C1_TOOL_NOT_ALLOWED``);
2. the arguments pass the tool's schema, including typed identifiers (``C1_SCHEMA``);
3. target validation against trusted context: host in the asset inventory
   (``C1_UNKNOWN_HOST``), account in the identity directory (``C1_UNKNOWN_ACCOUNT``), IP not in an
   internal range (``C1_INTERNAL_IP``) and not a protected address (``C1_PROTECTED_IP``);
4. the typed-argument rule: a PID, IP or hash must equal a typed canonical field of a record in
   the episode's retrieved-record registry, re-read from the database (``C1_UNPROVENANCED_VALUE``).

For ``request_approval`` the embedded action gets the same target and typed-argument checks
(§D.6.2a); other escalation and read-only tools get the schema check only.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb
from pydantic import BaseModel, ValidationError

from gbya.context.models import TrustedContext, account_key, host_key
from gbya.data.fieldmap import acting_user_column
from gbya.gate.evidence import (
    MAX_CITED,
    MAX_EVIDENCE_TOKENS,
    CitedRecord,
    RenderedEvidence,
    render_cited,
)
from gbya.gate.types import CheckName, CheckResult
from gbya.llm.tokens import TokenCounter
from gbya.policy.engine import PolicyDecision, PolicyEngine
from gbya.tools import provenance
from gbya.tools.registry import ARG_MODELS, RequestApprovalArgs
from gbya.tools.state import EpisodeState


def _result(t0: float, passed: bool, code: str, message: str, **details: Any) -> CheckResult:
    return CheckResult(
        check="C1",
        passed=passed,
        code=code,
        message=message,
        details=details,
        duration_ms=round((time.perf_counter() - t0) * 1000, 3),
    )


def _schema(tool: str, raw_args: Mapping[str, Any]) -> BaseModel | list[dict[str, Any]]:
    model = ARG_MODELS.get(tool)
    if model is None:
        return [{"loc": ["tool"], "msg": f"unknown tool {tool}"}]
    try:
        return model.model_validate(dict(raw_args))
    except ValidationError as exc:
        return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]


def check_schema(tool: str, raw_args: Mapping[str, Any]) -> CheckResult:
    """Schema-only C1 (G0: an unparseable call cannot be dispatched, A-7)."""
    t0 = time.perf_counter()
    parsed = _schema(tool, raw_args)
    if isinstance(parsed, list):
        return _result(t0, False, "C1_SCHEMA", f"Arguments for {tool} are invalid.", errors=parsed)
    return _result(t0, True, "OK", "Arguments are valid.")


def _target_problem(
    target: Mapping[str, Any],
    ctx: TrustedContext,
    state: EpisodeState,
    con: duckdb.DuckDBPyConnection,
) -> tuple[str, str, dict[str, Any]] | None:
    host, account = target.get("host"), target.get("account")
    if host is not None and ctx.asset(str(host)) is None:
        return "C1_UNKNOWN_HOST", f"Host {host} is not in the asset inventory.", {"host": host}
    if account is not None and ctx.identity(str(account)) is None:
        return (
            "C1_UNKNOWN_ACCOUNT",
            f"Account {account} is not in the identity directory.",
            {"account": account},
        )
    ip = target.get("ip")
    if ip is not None:
        addr = ipaddress.ip_address(str(ip))
        if ctx.network.is_internal(addr):
            return (
                "C1_INTERNAL_IP",
                f"{ip} is an internal address; it cannot be blocked.",
                {"ip": ip},
            )
        if ctx.network.is_protected(addr):
            return (
                "C1_PROTECTED_IP",
                f"{ip} is a protected address; it cannot be blocked.",
                {"ip": ip},
            )
    # Typed-argument rule (provenance of identifiers taken from logs).
    pid = target.get("pid")
    if pid is not None and not provenance.pid_provenanced(con, state.retrieved, int(pid)):
        return (
            "C1_UNPROVENANCED_VALUE",
            f"PID {pid} does not appear as a PID in any record you have retrieved.",
            {"field": "pid", "value": pid},
        )
    if ip is not None and not provenance.ip_provenanced(con, state.retrieved, str(ip)):
        return (
            "C1_UNPROVENANCED_VALUE",
            f"IP {ip} does not appear as an IP address in any record you have retrieved.",
            {"field": "ip", "value": ip},
        )
    digest = target.get("hash")
    if digest is not None and not provenance.hash_provenanced(con, state.retrieved, str(digest)):
        return (
            "C1_UNPROVENANCED_VALUE",
            f"Hash {digest} does not appear in any record you have retrieved.",
            {"field": "hash", "value": digest},
        )
    return None


def check_c1(
    tool: str,
    raw_args: Mapping[str, Any],
    *,
    allowed_tools: Collection[str],
    ctx: TrustedContext,
    state: EpisodeState,
    con: duckdb.DuckDBPyConnection,
) -> CheckResult:
    t0 = time.perf_counter()
    if tool not in allowed_tools:
        return _result(t0, False, "C1_TOOL_NOT_ALLOWED", f"Tool {tool} is not allowed.", tool=tool)
    parsed = _schema(tool, raw_args)
    if isinstance(parsed, list):
        return _result(t0, False, "C1_SCHEMA", f"Arguments for {tool} are invalid.", errors=parsed)

    target: dict[str, Any] | None = None
    if isinstance(parsed, RequestApprovalArgs):
        try:
            target = parsed.action.target().model_dump()
        except ValidationError as exc:
            errors = [{"loc": ["action", *e["loc"]], "msg": e["msg"]} for e in exc.errors()]
            return _result(
                t0, False, "C1_SCHEMA", "The action to approve is invalid.", errors=errors
            )
    elif tool in ("isolate_host", "kill_process", "disable_account", "block_ip"):
        target = parsed.model_dump(exclude={"cited"})

    if target is not None:
        problem = _target_problem(target, ctx, state, con)
        if problem is not None:
            code, message, details = problem
            return _result(t0, False, code, message, **details)
    return _result(t0, True, "OK", "Tool, arguments and targets are valid.")


# ---------------------------------------------------------------- C2, C3, C5 (T2.5)


@dataclass(frozen=True)
class GateEnv:
    """What the gate may consult for one episode (all trusted or canonical)."""

    con: duckdb.DuckDBPyConnection
    ctx: TrustedContext
    state: EpisodeState
    counter: TokenCounter
    allowed_tools: Collection[str]
    window: tuple[datetime, datetime] | None


def _timed(
    t0: float, check: CheckName, passed: bool, code: str, message: str, **details: Any
) -> CheckResult:
    return CheckResult(
        check=check,
        passed=passed,
        code=code,
        message=message,
        details=details,
        duration_ms=round((time.perf_counter() - t0) * 1000, 3),
    )


def check_c2(
    cited: list[int], records: Mapping[int, CitedRecord], env: GateEnv
) -> tuple[CheckResult, RenderedEvidence | None]:
    """Citation scope: non-empty, existing, inside the window, retrieved, within budget."""
    t0 = time.perf_counter()
    if not cited:
        return _timed(
            t0, "C2", False, "C2_EMPTY", "Cite the record ids that support the action."
        ), None
    unknown = [i for i in cited if i not in records]
    if unknown:
        return _timed(
            t0, "C2", False, "C2_UNKNOWN_ID", f"Records {unknown} do not exist.", ids=unknown
        ), None
    if env.window is not None:
        lo, hi = env.window
        outside = [i for i in cited if (ts := records[i].ts) is not None and not lo <= ts <= hi]
        if outside:
            return _timed(
                t0, "C2", False, "C2_OUT_OF_WINDOW", f"Records {outside} are outside the window.",
                ids=outside,
            ), None  # fmt: skip
    not_seen = [i for i in cited if i not in env.state.retrieved]
    if not_seen:
        return _timed(
            t0, "C2", False, "C2_NOT_RETRIEVED",
            f"Records {not_seen} were not retrieved by your queries; query them first.",
            ids=not_seen,
        ), None  # fmt: skip
    if len(cited) > MAX_CITED:
        return _timed(
            t0, "C2", False, "C2_EVIDENCE_TOO_LARGE",
            f"{len(cited)} records cited; cite at most {MAX_CITED}.",
            records=len(cited), limit=MAX_CITED,
        ), None  # fmt: skip
    ordered = [records[i] for i in dict.fromkeys(cited)]
    evidence = render_cited(ordered, env.counter)
    if evidence.tokens > MAX_EVIDENCE_TOKENS:
        return _timed(
            t0, "C2", False, "C2_EVIDENCE_TOO_LARGE",
            f"The cited records take {evidence.tokens} tokens; the limit is "
            f"{MAX_EVIDENCE_TOKENS}. Cite fewer or shorter records (nothing is trimmed).",
            tokens=evidence.tokens, limit=MAX_EVIDENCE_TOKENS,
        ), None  # fmt: skip
    return _timed(t0, "C2", True, "OK", "Citations are valid."), evidence


# PID roles for kill_process (§D.6.2a): which columns make P the actor of the record.
ACTOR_PID: dict[str, tuple[str, ...]] = {
    "process_create": ("pid",),
    "process_access": ("source_pid",),
    "network": ("pid",),
    "registry": ("pid",),
    "file": ("pid",),
}
NON_ACTOR_PID: dict[str, tuple[str, ...]] = {
    "process_create": ("ppid",),
    "process_access": ("target_pid",),
}


def _acting_user(rec: CitedRecord) -> str | None:
    if rec.table is None:
        return None
    col = acting_user_column(rec.table, int(rec.values.get("event_id") or 0))
    v = rec.values.get(col)
    return account_key(str(v)) if v else None


def check_c3(tool: str, args: Mapping[str, Any], records: list[CitedRecord]) -> CheckResult:
    """Target match on cited records re-read from the canonical database."""
    t0 = time.perf_counter()
    if tool == "isolate_host":
        h = host_key(str(args["host"]))
        if any(r.host and host_key(r.host) == h for r in records):
            return _timed(t0, "C3", True, "OK", "A cited record is on the target host.")
        seen = sorted({r.host for r in records if r.host})
        return _timed(
            t0, "C3", False, "C3_HOST_MISMATCH",
            f"No cited record is on host {args['host']} (cited records are on {seen}).",
            expected=args["host"], found=seen,
        )  # fmt: skip
    if tool == "disable_account":
        u = account_key(str(args["account"]))
        users = sorted({x for r in records if (x := _acting_user(r))})
        if u in users:
            return _timed(t0, "C3", True, "OK", "A cited record shows the account acting.")
        return _timed(
            t0, "C3", False, "C3_ACTING_USER_MISMATCH",
            f"No cited record shows account {args['account']} acting (acting users: {users}).",
            field="acting_user", expected=args["account"], found=users,
        )  # fmt: skip
    if tool == "kill_process":
        h, pid = host_key(str(args["host"])), int(args["pid"])
        on_host = [r for r in records if r.table and r.host and host_key(r.host) == h]
        if any(r.values.get(c) == pid for r in on_host for c in ACTOR_PID.get(str(r.table), ())):
            return _timed(t0, "C3", True, "OK", "A cited record shows the process acting.")
        if any(
            r.values.get(c) == pid for r in on_host for c in NON_ACTOR_PID.get(str(r.table), ())
        ):
            return _timed(
                t0, "C3", False, "C3_PID_ROLE_MISMATCH",
                f"PID {pid} appears in the cited records only as a target or parent, not as the "
                "process that acted.",
                pid=pid,
            )  # fmt: skip
        return _timed(
            t0, "C3", False, "C3_PID_NOT_FOUND",
            f"PID {pid} does not appear in any cited record on host {args['host']}.", pid=pid,
        )  # fmt: skip
    if tool == "block_ip":
        want = ipaddress.ip_address(str(args["ip"]))
        for r in records:
            dst = r.values.get("dst_ip") if r.table == "network" else None
            try:
                if dst is not None and ipaddress.ip_address(str(dst)) == want:
                    return _timed(t0, "C3", True, "OK", "A cited network record goes to the IP.")
            except ValueError:
                continue
        return _timed(
            t0, "C3", False, "C3_NO_NETWORK_RECORD",
            f"No cited network record has destination {args['ip']}.", ip=args["ip"],
        )  # fmt: skip
    raise ValueError(f"C3 applies to state-changing tools only, not {tool!r}")


def check_c5(
    tool: str, args: Mapping[str, Any], ctx: TrustedContext, policy: PolicyEngine
) -> tuple[CheckResult, PolicyDecision]:
    t0 = time.perf_counter()
    target = {k: v for k, v in args.items() if k != "cited"}
    d = policy.evaluate(tool, target, ctx)
    if d.decision == "forbidden":
        return _timed(
            t0, "C5", False, "C5_FORBIDDEN", f"Policy forbids this action ({d.rule_id}).",
            rule=d.rule_id,
        ), d  # fmt: skip
    return _timed(t0, "C5", True, d.rule_id, f"Policy: {d.decision} ({d.rule_id}).",
                  decision=d.decision), d  # fmt: skip
