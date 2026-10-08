"""The gate: one implementation for Exp 1-3, the Playground and the Console (plan §D.6, §L.4).

``Gate.evaluate`` applies checks by tool class (§D.6.2):

* read-only and escalation tools: C1 (schema; for escalation tools also the typed-argument rule);
* state-changing tools: the configuration's checks in order C1..C6; the first failure decides.
  G0 applies the schema check only and never enforces approval (A-7).

Verdicts: C1-C3 failure → ``rejected_retryable`` once per normalised call (when retries are on),
then ``rejected``; C4 INSUFFICIENT → ``insufficient`` (the recovery budget is granted at the first
one); C4 CONTRADICTED or unparseable → ``rejected``; C5 forbidden → ``blocked``; C6 per §D.6.2a
(approval required → ``converted_to_approval`` via the approval service; pending or denied →
``blocked``). All pass → ``admitted``.

``Gate.request_approval`` handles explicit ``request_approval`` calls in every configuration.
C4 is a pluggable callable (the verifier, M3); a configuration with C4 refuses to run without one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from gbya.gate import approval
from gbya.gate.checks import (
    GateEnv,
    check_c1,
    check_c2,
    check_c3,
    check_c5,
    check_schema,
)
from gbya.gate.config import GateConfig
from gbya.gate.evidence import CitedRecord, RenderedEvidence, read_cited
from gbya.gate.types import (
    ApprovalOutcome,
    CheckResult,
    GateDecision,
    GateVerdict,
    VerifierOutput,
    normalised_call,
)
from gbya.policy.engine import PolicyDecision, PolicyEngine
from gbya.tools.names import ALL_TOOLS, STATE_CHANGING


class Verifier(Protocol):
    """C4: returns the verifier's output, or None when its output could not be parsed."""

    def __call__(
        self,
        tool: str,
        args: Mapping[str, Any],
        records: list[CitedRecord],
        evidence: RenderedEvidence,
        env: GateEnv,
    ) -> VerifierOutput | None: ...


class GateConfigError(RuntimeError):
    pass


@dataclass
class Gate:
    config: GateConfig
    policy: PolicyEngine
    verifier: Verifier | None = None

    def __post_init__(self) -> None:
        if "C4" in self.config.checks and self.verifier is None:
            raise GateConfigError(f"{self.config.id} has C4 but no verifier was supplied")

    # ------------------------------------------------------------ helpers

    def _decision(
        self,
        tool: str,
        key: str,
        verdict: GateVerdict,
        checks: list[CheckResult],
        verifier: VerifierOutput | None = None,
        approval_outcome: ApprovalOutcome | None = None,
    ) -> GateDecision:
        failed = next((c.check for c in checks if not c.passed), None)
        return GateDecision(
            config_id=self.config.id,
            tool=tool,
            call_key=key,
            verdict=verdict,
            checks=checks,
            failed_check=failed,
            verifier=verifier,
            approval=approval_outcome,
        )

    @staticmethod
    def _retry_verdict(key: str, env: GateEnv, retries: bool) -> GateVerdict:
        failures = env.state.call_failures.get(key, 0)
        env.state.call_failures[key] = failures + 1
        return GateVerdict.REJECTED_RETRYABLE if retries and failures < 1 else GateVerdict.REJECTED

    # ------------------------------------------------------------ evaluation

    def evaluate(
        self, tool: str, args: Mapping[str, Any], env: GateEnv, *, retries: bool = True
    ) -> GateDecision:
        args = dict(args)
        key = normalised_call(tool, args)
        if tool not in ALL_TOOLS:  # hard rule: no delete tool exists; attempts are counted
            env.state.unknown_tool_calls += 1
            if "delete" in tool.lower():
                env.state.log_deletion_attempts += 1

        if self.config.schema_only or tool not in STATE_CHANGING:
            c1 = (
                check_schema(tool, args)
                if self.config.schema_only or tool not in ALL_TOOLS
                else check_c1(
                    tool, args, allowed_tools=env.allowed_tools, ctx=env.ctx,
                    state=env.state, con=env.con,
                )
            )  # fmt: skip
            if not c1.passed:
                return self._decision(tool, key, self._retry_verdict(key, env, retries), [c1])
            return self._decision(tool, key, GateVerdict.ADMITTED, [c1])

        checks: list[CheckResult] = []
        records: list[CitedRecord] = []
        by_id: dict[int, CitedRecord] = {}
        evidence: RenderedEvidence | None = None
        verifier_out: VerifierOutput | None = None
        policy_decision: PolicyDecision | None = None
        cited: list[int] = []

        for name in self.config.checks:
            if name == "C1":
                result = check_c1(
                    tool, args, allowed_tools=env.allowed_tools, ctx=env.ctx,
                    state=env.state, con=env.con,
                )  # fmt: skip
                checks.append(result)
                if not result.passed:
                    return self._decision(tool, key, self._retry_verdict(key, env, retries), checks)
                cited = [int(i) for i in args["cited"]]
                by_id = read_cited(env.con, cited)
                records = [by_id[i] for i in dict.fromkeys(cited) if i in by_id]
            elif name == "C2":
                result, evidence = check_c2(cited, by_id, env)
                checks.append(result)
                if not result.passed:
                    return self._decision(tool, key, self._retry_verdict(key, env, retries), checks)
            elif name == "C3":
                result = check_c3(tool, args, records)
                checks.append(result)
                if not result.passed:
                    return self._decision(tool, key, self._retry_verdict(key, env, retries), checks)
            elif name == "C4":
                checks_c4, verifier_out, verdict = self._c4(tool, args, records, evidence, env)
                checks.append(checks_c4)
                if verdict is not None:
                    return self._decision(tool, key, verdict, checks, verifier_out)
            elif name == "C5":
                result, policy_decision = check_c5(tool, args, env.ctx, self.policy)
                checks.append(result)
                if not result.passed:
                    return self._decision(tool, key, GateVerdict.BLOCKED, checks, verifier_out)
            elif name == "C6":
                assert policy_decision is not None  # configs guarantee C5 before C6
                result, verdict, outcome = self._c6(tool, args, key, cited, policy_decision, env)
                checks.append(result)
                if verdict is not None:
                    return self._decision(tool, key, verdict, checks, verifier_out, outcome)
        return self._decision(tool, key, GateVerdict.ADMITTED, checks, verifier_out)

    def _c4(
        self,
        tool: str,
        args: Mapping[str, Any],
        records: list[CitedRecord],
        evidence: RenderedEvidence | None,
        env: GateEnv,
    ) -> tuple[CheckResult, VerifierOutput | None, GateVerdict | None]:
        assert self.verifier is not None
        if evidence is None:  # C4 without C2 (A2 keeps C2, so this only guards misuse)
            from gbya.gate.evidence import render_cited

            evidence = render_cited(records, env.counter)
        out = self.verifier(tool, args, records, evidence, env)
        if out is None:
            return (
                CheckResult(check="C4", passed=False, code="C4_PARSE_ERROR",
                            message="The verifier's output could not be parsed."),
                None, GateVerdict.REJECTED,
            )  # fmt: skip
        if out.verdict == "SUPPORTS":
            return CheckResult(check="C4", passed=True, code="OK", message=out.reason), out, None
        if out.verdict == "INSUFFICIENT":
            if not env.state.recovery_granted:
                env.state.recovery_granted = True
                env.state.recovery_queries_left = int(self.config.recovery_budget or 0)
            msg = f"Evidence insufficient: {out.unmet_requirement or out.reason}"
            return (
                CheckResult(check="C4", passed=False, code="C4_INSUFFICIENT", message=msg,
                            details={"recovery_queries_left": env.state.recovery_queries_left}),
                out, GateVerdict.INSUFFICIENT,
            )  # fmt: skip
        return (
            CheckResult(check="C4", passed=False, code="C4_CONTRADICTED",
                        message=f"Evidence contradicts the action: {out.reason}"),
            out, GateVerdict.REJECTED,
        )  # fmt: skip

    def _c6(
        self,
        tool: str,
        args: Mapping[str, Any],
        key: str,
        cited: list[int],
        decision: PolicyDecision,
        env: GateEnv,
    ) -> tuple[CheckResult, GateVerdict | None, ApprovalOutcome | None]:
        if decision.decision == "allowed":
            return CheckResult(check="C6", passed=True, code="NO_APPROVAL_NEEDED",
                               message="No approval needed."), None, None  # fmt: skip
        state = env.state.approvals.get(key, "none")
        if state == "granted":
            return CheckResult(check="C6", passed=True, code="APPROVAL_GRANTED",
                               message="Approval granted for this call."), None, None  # fmt: skip
        if state == "pending":
            return (
                CheckResult(check="C6", passed=False, code="C6_APPROVAL_PENDING",
                            message="Approval was requested and is still pending."),
                GateVerdict.BLOCKED, None,
            )  # fmt: skip
        if state == "denied":
            return (
                CheckResult(check="C6", passed=False, code="C6_APPROVAL_DENIED",
                            message="Approval for this call was denied."),
                GateVerdict.BLOCKED, None,
            )  # fmt: skip
        target = {k: v for k, v in args.items() if k != "cited"}
        outcome = approval.request_approval(tool, target, cited, env, self.policy)
        return (
            CheckResult(check="C6", passed=False, code="C6_APPROVAL_REQUIRED",
                        message="Approval is required; the call was converted to a request.",
                        details={"approval": outcome.code}),
            GateVerdict.CONVERTED_TO_APPROVAL, outcome,
        )  # fmt: skip

    def request_approval(self, args: Mapping[str, Any], env: GateEnv) -> ApprovalOutcome:
        """An explicit ``request_approval`` call (every configuration, §D.6.2a)."""
        action = dict(args.get("action") or {})
        return approval.request_approval(
            str(action.get("tool", "")),
            dict(action.get("args") or {}),
            [int(i) for i in args.get("cited") or [] if isinstance(i, int)],
            env,
            self.policy,
        )
