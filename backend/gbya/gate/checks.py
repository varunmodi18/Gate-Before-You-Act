"""Gate checks (plan §D.6.2). C1 now (T2.4); C2, C3, C5, C6 and the orchestrator in T2.5.

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
from typing import Any

import duckdb
from pydantic import BaseModel, ValidationError

from gbya.context.models import TrustedContext
from gbya.gate.types import CheckResult
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
