"""Tool dispatchers: how the episode loop reaches the tools (plan §D.5.3, T5.8).

The episode loop keeps the LLM, the history, termination and the caps; a ``ToolDispatcher`` does
everything on the tool side of one proposed call — the gate (``Gate.evaluate``; explicit
``request_approval`` through the approval service), the recovery budget, the read-only tools, the
mock state-changing actions and the escalation records — and writes the trace steps of the call.

* ``InProcessDispatcher``: direct function calls (every configuration except A8).
* ``McpDispatcher`` (``gbya.agent.mcp_client``): the same calls over MCP; the server
  (``gbya.tools.mcp_server``) runs an ``InProcessDispatcher`` in its tool handler, so the gate
  runs server-side and no tool is reachable without it.

``CallRequest`` and ``CallOutcome`` are JSON-serialisable, so both transports carry the same data.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from gbya.gate.checks import GateEnv
from gbya.gate.gate import Gate
from gbya.gate.types import ApprovalOutcome, Claim, GateDecision, GateVerdict, normalised_call
from gbya.tools import escalation, mock_actions
from gbya.tools.names import STATE_CHANGING, TAKES_CITED
from gbya.tools.registry import REGISTRY, AskAnalystArgs, DraftReportArgs, ToolEnvironment

RECOVERY_EXHAUSTED = "RECOVERY_BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class CallRequest:
    """One proposed tool call, with the agent's claim (shown to the verifier as a claim only)."""

    tool: str
    args: dict[str, Any]
    cited: list[int] = field(default_factory=list)
    technique_id: str | None = None
    thought: str = ""

    def call_args(self) -> dict[str, Any]:
        if self.tool in TAKES_CITED:
            return {**self.args, "cited": list(self.cited)}
        return dict(self.args)

    def claim(self) -> Claim:
        return Claim(technique_claimed=self.technique_id, rationale=self.thought or None)


@dataclass
class CallOutcome:
    """What happened to one call, for the loop (feedback, termination hints, episode state)."""

    feedback: str  # what the model sees next: tool content, or gate / approval feedback
    gate_feedback: (
        bool  # True: gate, approval or refusal feedback (a "feedback" step; never stubbed)
    )
    is_error: bool  # the call was not carried out
    returned_ids: list[int] = field(default_factory=list)
    executed: dict[str, Any] | None = None  # a state-changing call that was executed (mock)
    handoff: dict[str, Any] | None = None  # an accepted ask_analyst / draft_report record
    approvals: list[dict[str, Any]] = field(default_factory=list)  # ApprovalOutcome JSON
    state: dict[str, Any] = field(default_factory=dict)  # approvals and retrieved registry
    events: list[dict[str, Any]] = field(default_factory=list)  # trace calls (MCP replay)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class ToolDispatcher(Protocol):
    transport: str

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call(self, request: CallRequest) -> CallOutcome: ...

    def state(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


def tool_list() -> list[dict[str, Any]]:
    """The registry's tools: name, description and the JSON schema of their Pydantic model."""
    return [{"name": s.name, "description": s.description, "input_schema": s.json_schema()}
            for s in REGISTRY.values()]  # fmt: skip


class _Recorder:
    """A trace that keeps its calls as JSON events (the MCP server returns them to the loop)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._ids = 0

    def step(self, kind: str, payload: dict[str, Any], ms: float | None = None) -> int:
        self.events.append({"type": "step", "kind": kind, "payload": payload, "ms": ms})
        return len(self.events)

    def gate_decision(
        self, decision: GateDecision, call: dict[str, Any], cited: list[int], ms: float
    ) -> int | None:
        self._ids += 1
        self.events.append({"type": "gate_decision", "local_id": self._ids,
                            "decision": decision.model_dump(
                                mode="json", exclude={"verifier_call": {"messages"}}),
                            "call": call, "cited": cited, "ms": ms})  # fmt: skip
        return self._ids

    def tool_call(self, tool: str, args: dict[str, Any], cited: list[int], *, admitted: bool,
                  gate_decision_id: int | None) -> None:  # fmt: skip
        self.events.append({"type": "tool_call", "tool": tool, "args": args, "cited": cited,
                            "admitted": admitted,
                            "gate_decision_id": gate_decision_id})  # fmt: skip


class InProcessDispatcher:
    """The tool side of an episode in this process. ``trace`` is the episode's trace, or a
    recorder whose events travel back to the loop (MCP)."""

    transport = "in_process"

    def __init__(
        self,
        gate: Gate,
        env: GateEnv,
        tools: ToolEnvironment,
        trace: Any,
        recorder: mock_actions.Recorder | None = None,
        retries: bool = True,
    ) -> None:
        assert tools.state is env.state, "the tools and the gate share one EpisodeState"
        self.gate, self.env, self.tools, self.trace = gate, env, tools, trace
        self.recorder = recorder or mock_actions.MemoryRecorder()
        self.retries = retries

    @classmethod
    def recording(cls, gate: Gate, env: GateEnv, tools: ToolEnvironment,
                  retries: bool = True) -> InProcessDispatcher:  # fmt: skip
        return cls(gate, env, tools, _Recorder(), None, retries)

    def list_tools(self) -> list[dict[str, Any]]:
        return tool_list()

    def state(self) -> dict[str, Any]:
        s = self.env.state
        return {"approvals": dict(s.approvals), "retrieved": sorted(s.retrieved)}

    def close(self) -> None:
        return None

    def call(self, req: CallRequest) -> CallOutcome:
        if isinstance(self.trace, _Recorder):
            self.trace.events = []
        out = self._call(req)
        out.state = self.state()
        if isinstance(self.trace, _Recorder):
            out.events = list(self.trace.events)
        return out

    # ------------------------------------------------------------------ the call

    def _call(self, req: CallRequest) -> CallOutcome:
        if req.tool == "request_approval":
            return self._request_approval(req)
        if req.tool == "sql_query" and self._recovery_exhausted():
            self.trace.step("tool", {"tool": req.tool, "args": req.args, "ok": False,
                                     "error_code": RECOVERY_EXHAUSTED})  # fmt: skip
            self.trace.tool_call(req.tool, req.args, [], admitted=False, gate_decision_id=None)
            return CallOutcome(
                f"{RECOVERY_EXHAUSTED}: the recovery queries after INSUFFICIENT evidence are "
                "used up. Act on the evidence you have, escalate or end the episode.",
                gate_feedback=True, is_error=True,
            )  # fmt: skip

        t0 = time.perf_counter()
        decision = self.gate.evaluate(req.tool, req.call_args(), self.env, retries=self.retries,
                                      claim=req.claim())  # fmt: skip
        ms = round((time.perf_counter() - t0) * 1000, 3)
        self.trace.step("gate", {"decision": decision.model_dump(
            mode="json", exclude={"verifier_call": {"messages"}})}, ms)  # fmt: skip
        gid = (self.trace.gate_decision(decision, {"tool": req.tool, "args": req.args},
                                        list(req.cited), ms)
               if req.tool in STATE_CHANGING else None)  # fmt: skip
        cited = list(req.cited) if "cited" in req.call_args() else []
        approvals = []
        if decision.verdict is GateVerdict.CONVERTED_TO_APPROVAL and decision.approval:
            approvals.append(self._approval_step(decision.approval, "c6"))
        self.trace.tool_call(req.tool, req.args, cited, admitted=decision.admitted,
                             gate_decision_id=gid)  # fmt: skip
        if not decision.admitted:
            text = decision.message
            if decision.verdict is GateVerdict.CONVERTED_TO_APPROVAL:
                text = f"C6_APPROVAL_REQUIRED: not executed; approval was requested. {text}"
            return CallOutcome(text, gate_feedback=True, is_error=True, approvals=approvals)
        if req.tool in STATE_CHANGING:
            return self._execute(req, gid)
        if req.tool in ("ask_analyst", "draft_report"):
            return self._hand_off(req)
        return self._read_only(req)

    def _approval_step(self, outcome: ApprovalOutcome, via: str) -> dict[str, Any]:
        data = outcome.model_dump(mode="json")
        self.trace.step("approval", {"via": via, **data})
        return data

    def _recovery_exhausted(self) -> bool:
        state = self.env.state
        if not state.recovery_granted:
            return False
        if state.recovery_queries_left <= 0:
            return True
        state.recovery_queries_left -= 1
        return False

    def _read_only(self, req: CallRequest) -> CallOutcome:
        t0 = time.perf_counter()
        res = self.tools.run_read_only(req.tool, req.args)
        ms = round((time.perf_counter() - t0) * 1000, 3)
        self.trace.step("tool", {"tool": req.tool, "args": req.args, "ok": res.ok,
                                 "error_code": res.error_code, "content": res.content,
                                 "registered": sorted(res.registered)}, ms)  # fmt: skip
        return CallOutcome(res.content, gate_feedback=False, is_error=not res.ok,
                           returned_ids=sorted(res.registered))  # fmt: skip

    def _execute(self, req: CallRequest, gid: int | None) -> CallOutcome:
        key = normalised_call(req.tool, req.args)
        approval_state = self.env.state.approvals.get(key, "none")
        action = mock_actions.execute(req.tool, req.args, list(req.cited), self.recorder, gid)
        self.trace.step("tool", {"tool": req.tool, "args": req.args, "cited": list(req.cited),
                                 "ok": True, "content": action.message,
                                 "approval_state": approval_state, "call_key": key})  # fmt: skip
        executed = {**asdict(action), "cited": list(action.cited), "call_key": key}
        return CallOutcome(action.message, gate_feedback=False, is_error=False, executed=executed)

    def _hand_off(self, req: CallRequest) -> CallOutcome:
        shown = self.env.state.shown_untrusted
        try:
            record = (
                escalation.ask_analyst(AskAnalystArgs.model_validate(req.args), shown)
                if req.tool == "ask_analyst"
                else escalation.draft_report(DraftReportArgs.model_validate(req.call_args()), shown)
            )
        except ValidationError as exc:  # C1 admitted it, so this is a programming error
            raise AssertionError(f"admitted {req.tool} failed validation: {exc}") from exc
        self.trace.step("tool", {"tool": req.tool, "args": req.args, "ok": True,
                                 "escalation": asdict(record)})  # fmt: skip
        return CallOutcome(record.message, gate_feedback=False, is_error=False,
                           handoff=asdict(record))  # fmt: skip

    def _request_approval(self, req: CallRequest) -> CallOutcome:
        t0 = time.perf_counter()
        outcome = self.gate.request_approval(req.call_args(), self.env)
        ms = round((time.perf_counter() - t0) * 1000, 3)
        data = self._approval_step(outcome, "explicit")
        self.trace.tool_call(req.tool, req.args, list(req.cited), admitted=outcome.accepted,
                             gate_decision_id=None)  # fmt: skip
        self.trace.step("tool", {"tool": req.tool, "args": req.args, "ok": outcome.accepted,
                                 "content": outcome.message}, ms)  # fmt: skip
        text = outcome.message if outcome.accepted else f"{outcome.code}: {outcome.message}"
        return CallOutcome(text, gate_feedback=True, is_error=not outcome.accepted,
                           approvals=[data])  # fmt: skip
