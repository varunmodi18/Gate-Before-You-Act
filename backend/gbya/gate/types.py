"""Gate data types (plan §D.6.1)."""

from __future__ import annotations

import ipaddress
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gbya.context.models import account_key, host_key

CheckName = Literal["C1", "C2", "C3", "C4", "C5", "C6"]


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: CheckName
    passed: bool
    code: str  # e.g. "C3_HOST_MISMATCH"; "OK" (or the policy rule id for C5) when passed
    message: str  # shown to the agent (feedback) and the UI
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class GateVerdict(StrEnum):
    ADMITTED = "admitted"
    REJECTED_RETRYABLE = "rejected_retryable"  # C1-C3 failure, retry left
    REJECTED = "rejected"  # C1-C3 failure without retry, or C4 CONTRADICTED / parse error
    INSUFFICIENT = "insufficient"  # C4 INSUFFICIENT; recovery budget may remain
    BLOCKED = "blocked"  # C5 forbidden, or C6 with approval pending or denied
    CONVERTED_TO_APPROVAL = "converted_to_approval"  # C6: turned into request_approval


class TicketScope(BaseModel):
    model_config = ConfigDict(frozen=True)
    applies: bool
    matches: dict[str, bool] = Field(default_factory=dict)  # host, account, command, time


class VerifierOutput(BaseModel):
    """C4's structured output (§D.7). Produced by the verifier in M3; scripted in tests."""

    model_config = ConfigDict(frozen=True)
    verdict: Literal["SUPPORTS", "INSUFFICIENT", "CONTRADICTED"]
    unmet_requirement: str | None = None
    ticket_scope: TicketScope | None = None
    reason: str = ""


class Claim(BaseModel):
    """The agent's claim that travels with a call (§D.3 information boundary).

    ``technique_claimed`` is shown to the verifier as a claim and is never a retrieval or lookup
    key; ``rationale`` is shown only to the ``rationale`` verifier variant (A3)."""

    model_config = ConfigDict(frozen=True)
    technique_claimed: str | None = None
    rationale: str | None = None


class VerifierCall(BaseModel):
    """One C4 call with its I/O, stored with the gate decision (T3.3) and in ``verifier_evals``.

    ``output`` is None when the model's output could not be parsed (``C4_PARSE_ERROR``)."""

    model_config = ConfigDict(frozen=True)
    variant: str
    output: VerifierOutput | None
    error: str | None = None
    raw: Any = None  # the parsed object, or the raw text when it did not parse
    prompt_hash: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] | None = None  # mode, query hash and rankings
    tokens_in: int = 0
    tokens_out: int = 0
    ms: float = 0.0


class ApprovalOutcome(BaseModel):
    """Result of a request_approval, explicit or converted by C6 (§D.6.2a second table)."""

    model_config = ConfigDict(frozen=True)
    accepted: bool  # accepted requests are escalation events for scoring
    code: str  # GRANTED / DENIED / NO_RESPONSE, or the refusal code
    state: Literal["none", "granted", "denied", "pending"]  # approval state of the call after
    call_key: str
    message: str  # shown to the agent


class GateDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    config_id: str
    tool: str
    call_key: str
    verdict: GateVerdict
    checks: list[CheckResult]
    failed_check: str | None = None
    verifier: VerifierOutput | None = None
    verifier_call: VerifierCall | None = None  # the C4 I/O, when C4 ran
    approval: ApprovalOutcome | None = None  # set when C6 converted the call

    @property
    def admitted(self) -> bool:
        return self.verdict is GateVerdict.ADMITTED

    @property
    def message(self) -> str:
        """Feedback for the agent: the deciding check's message, or the outcome."""
        if self.approval is not None:
            return self.approval.message
        failed = next((c for c in self.checks if not c.passed), None)
        if failed is not None:
            return f"{failed.code}: {failed.message}"
        return "Admitted." if self.admitted else self.verdict.value


def normalised_call(tool: str, args: dict[str, Any]) -> str:
    """Key of a call for approvals and retries: tool + normalised target (``cited`` excluded).

    Hosts compare case-insensitively, accounts without ``DOMAIN\\`` and lower-case, IPs in
    canonical form (same normalisation as scoring, T5.3).
    """
    target: dict[str, Any] = {}
    for k, v in args.items():
        if k == "cited":
            continue
        if k == "host" and isinstance(v, str):
            target[k] = host_key(v)
        elif k == "account" and isinstance(v, str):
            target[k] = account_key(v)
        elif k == "ip" and isinstance(v, str):
            try:
                target[k] = str(ipaddress.ip_address(v.strip()))
            except ValueError:
                target[k] = v
        else:
            target[k] = v
    return f"{tool}:{json.dumps(target, sort_keys=True, default=str)}"
