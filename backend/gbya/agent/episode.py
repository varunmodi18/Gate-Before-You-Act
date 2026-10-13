"""The episode loop (plan §D.10.1-D.10.3, T5.2).

Each step calls the proposer (one re-ask on an unusable output), dispatches the proposal and returns
the tool result or the gate's feedback as the next user message. Every call goes through the one
gate (``Gate.evaluate``; explicit ``request_approval`` through ``Gate.request_approval``); the loop
adds no approval rule of its own and only relays the gate's results (§D.6.2a).

**Termination (exactly these):** T1 an admitted call fulfils the request (same tool and normalised
target as the request's objective, A-24) → ``fulfilled``; T2 an accepted ``ask_analyst`` or
``draft_report`` → ``handed_off``; T3 the control action ``end_episode`` → ``ended``; T4 a cap →
``cap``. ``request_approval`` never ends an episode.

**Caps** (§D.10.1): proposer LLM calls (re-asks included), steps (dispatched proposals, and
turns whose output stayed unusable after the re-ask), and proposer input tokens (no call is made
when the tokens used so far, as the server reported them, plus the prompt's own count would pass
the cap). The verifier's tokens are on the gate decisions and do not count towards the episode
cap, so caps are identical across configurations.
A prompt that the history policy cannot bring under the per-request limit (§D.10.3) also ends the
episode as ``cap``.

**Recovery budget** (§D.6.2a): after the first ``INSUFFICIENT`` the gate sets
``recovery_queries_left`` from the configuration; each later ``sql_query`` uses one, and a query
beyond the budget is refused without running (``RECOVERY_BUDGET_EXHAUSTED``) so the agent must act
on what it has, escalate or end the episode.

Steps are written append-only through a ``Trace``: ``SqlTrace`` (episodes, steps, tool_calls and
gate_decisions rows) or ``MemoryTrace``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from gbya.agent.proposer import (
    PREFIX_LABEL,
    PROMPT_LIMIT,
    Prompt,
    Proposal,
    ProposerResult,
    Turn,
    build_messages,
    propose,
)
from gbya.cases.models import OBJECTIVE_TOOL, Request
from gbya.experiments.exp1 import verifier_summary
from gbya.gate.checks import GateEnv
from gbya.gate.gate import Gate
from gbya.gate.types import ApprovalOutcome, GateDecision, normalised_call
from gbya.llm.client import LLMClient
from gbya.llm.schemas import Message
from gbya.llm.tokens import TokenCounter
from gbya.store.models import Episode, GateDecisionRow, Step, ToolCall
from gbya.tools import escalation, mock_actions
from gbya.tools.dispatcher import RECOVERY_EXHAUSTED as RECOVERY_EXHAUSTED  # re-exported
from gbya.tools.dispatcher import (
    CallRequest,
    InProcessDispatcher,
    ToolDispatcher,
)
from gbya.tools.names import tool_class
from gbya.tools.registry import ToolEnvironment

TerminalState = Literal["fulfilled", "handed_off", "ended", "cap"]


@dataclass(frozen=True)
class Caps:
    max_llm_calls: int = 8
    max_steps: int = 12
    max_input_tokens: int = 40_000  # proposer input tokens per episode (A-10)
    prompt_limit: int = PROMPT_LIMIT  # per request (§D.10.3)


def fulfils(request: Request, tool: str, args: dict[str, Any]) -> bool:
    """Whether a call achieves the request: the objective's tool on the requested target (A-24)."""
    tool_wanted = OBJECTIVE_TOOL[request.objective]
    return tool == tool_wanted and normalised_call(tool, args) == normalised_call(
        tool_wanted, request.target
    )


def prefix_text(prefix: dict[str, Any]) -> str:
    """The frozen prefix (``cases/prefix.py`` JSON) as the proposer sees it: the profile, each
    canned query with its result exactly as the tool rendered it, then the findings. Its
    ``retrieved`` IDs seed the episode's registry (the agent saw those records)."""
    parts = [str(prefix.get("profile", ""))]
    for i, q in enumerate(prefix.get("queries", []), 1):
        call = json.dumps({"sql": q["sql"]}, ensure_ascii=False)
        parts.append(f"Step {i}: sql_query {call}\nResult:\n{q['result']}")
    if prefix.get("findings"):
        parts.append(f"Findings: {prefix['findings']}")
    return "\n\n".join(p for p in parts if p)


# ---- trace --------------------------------------------------------------------------------------


class Trace(Protocol):
    def step(self, kind: str, payload: dict[str, Any], ms: float | None = None) -> int: ...

    def gate_decision(
        self, decision: GateDecision, call: dict[str, Any], cited: list[int], ms: float
    ) -> int | None: ...

    def tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        cited: list[int],
        *,
        admitted: bool,
        gate_decision_id: int | None,
    ) -> None: ...

    def finish(self, result: EpisodeResult, state: dict[str, Any]) -> None: ...


@dataclass
class MemoryTrace:
    """Keeps the episode in memory; ``persist`` then writes it in one short transaction (the Exp 2
    runner commits it together with the work unit's ``done`` status, so a killed worker leaves no
    partial episode and SQLite's write lock is held only for the write)."""

    steps: list[tuple[str, dict[str, Any], float | None]] = field(default_factory=list)
    decisions: list[GateDecision] = field(default_factory=list)
    decision_rows: list[tuple[dict[str, Any], list[int], float]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    result: EpisodeResult | None = None
    state: dict[str, Any] = field(default_factory=dict)
    transport: str = "in_process"  # recorded with the episode (A8: "mcp")

    def step(self, kind: str, payload: dict[str, Any], ms: float | None = None) -> int:
        self.steps.append((kind, payload, ms))
        return len(self.steps) - 1

    def gate_decision(
        self, decision: GateDecision, call: dict[str, Any], cited: list[int], ms: float
    ) -> int | None:
        self.decisions.append(decision)
        self.decision_rows.append((call, cited, ms))
        return len(self.decisions)  # a local id (1-based), mapped to the row id by ``persist``

    def tool_call(self, tool: str, args: dict[str, Any], cited: list[int], *, admitted: bool,
                  gate_decision_id: int | None) -> None:  # fmt: skip
        self.tool_calls.append({"tool": tool, "args": args, "cited": cited, "admitted": admitted,
                                "gate_decision_id": gate_decision_id})  # fmt: skip

    def finish(self, result: EpisodeResult, state: dict[str, Any]) -> None:
        self.result, self.state = result, state

    def kinds(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, p) for k, p, _ in self.steps]

    def persist(self, session: Session, episode: Episode) -> None:
        """Write the finished episode: the episode row (added and flushed here), its steps, gate
        decisions and tool calls."""
        assert self.result is not None, "the episode has not finished"
        session.add(episode)
        session.flush()
        for idx, (kind, payload, ms) in enumerate(self.steps):
            session.add(Step(episode_id=episode.id, idx=idx, kind=kind, payload=payload, ms=ms))
        ids: dict[int, int] = {}
        for local, (decision, (call, cited, ms)) in enumerate(
            zip(self.decisions, self.decision_rows, strict=True), 1
        ):
            row = _decision_row(episode, decision, call, cited, ms)
            session.add(row)
            session.flush()
            ids[local] = row.id
        for tc in self.tool_calls:
            gid = tc["gate_decision_id"]
            session.add(_tool_call_row(episode, tc["tool"], tc["args"], tc["cited"],
                                       tc["admitted"], ids.get(gid) if gid else None))  # fmt: skip
        _finish_episode(episode, self.result, self.state)
        episode.outcome_detail = {**(episode.outcome_detail or {}), "transport": self.transport}
        session.flush()


def _verifier_detail(decision: GateDecision) -> dict[str, Any] | None:
    call = decision.verifier_call
    summary = verifier_summary(call)
    if call is None or summary is None:
        return None
    return summary | {
        "manifest": call.manifest,
        "retrieval": call.retrieval,
        "ticket_scope_code": call.ticket_scope_code,
    }


def _decision_row(episode: Episode, decision: GateDecision, call: dict[str, Any],
                  cited: list[int], ms: float) -> GateDecisionRow:  # fmt: skip
    vcall = decision.verifier_call
    return GateDecisionRow(
        run_id=episode.run_id,
        case_id=episode.case_id,
        system=decision.config_id,
        run_idx=episode.run_idx,
        episode_id=episode.id,
        call=call,
        cited=cited,
        checks=[c.model_dump(mode="json") for c in decision.checks],
        verdict=decision.verdict.value,
        verifier=_verifier_detail(decision),
        tokens_in=vcall.tokens_in if vcall else 0,
        tokens_out=vcall.tokens_out if vcall else 0,
        ms=ms,
    )


def _tool_call_row(episode: Episode, tool: str, args: dict[str, Any], cited: list[int],
                   admitted: bool, gate_decision_id: int | None) -> ToolCall:  # fmt: skip
    return ToolCall(episode_id=episode.id, tool=tool, class_=tool_class(tool) or "unknown",
                    args=args, cited=cited, admitted=admitted,
                    gate_decision_id=gate_decision_id)  # fmt: skip


def _finish_episode(ep: Episode, result: EpisodeResult, state: dict[str, Any]) -> None:
    ep.status = "done"
    ep.terminal_state = result.terminal_state
    ep.budget_exhausted = result.terminal_state == "cap"
    ep.approvals = state["approvals"]
    ep.retrieved = state["retrieved"]
    ep.tokens_in = result.tokens_in
    ep.tokens_out = result.tokens_out
    ep.outcome_detail = {"episode": result.summary()}


@dataclass
class SqlTrace:
    """Writes the episode to app.db. Rows are only ever added; the episode row is updated at the
    end. ``commit`` after every step lets a live view (Console) follow the episode."""

    session: Session
    episode: Episode
    commit: bool = True
    defer_finish: bool = False  # the caller commits the finished episode (after scoring it)
    _idx: int = 0

    def _flush(self) -> None:
        if self.commit:
            self.session.commit()
        else:
            self.session.flush()

    def step(self, kind: str, payload: dict[str, Any], ms: float | None = None) -> int:
        idx = self._idx
        self.session.add(Step(episode_id=self.episode.id, idx=idx, kind=kind, payload=payload,
                              ms=ms))  # fmt: skip
        self._idx += 1
        self._flush()
        return idx

    def gate_decision(
        self, decision: GateDecision, call: dict[str, Any], cited: list[int], ms: float
    ) -> int | None:
        row = _decision_row(self.episode, decision, call, cited, ms)
        self.session.add(row)
        self.session.flush()
        return row.id

    def tool_call(self, tool: str, args: dict[str, Any], cited: list[int], *, admitted: bool,
                  gate_decision_id: int | None) -> None:  # fmt: skip
        self.session.add(_tool_call_row(self.episode, tool, args, cited, admitted,
                                        gate_decision_id))  # fmt: skip
        self.session.flush()

    def finish(self, result: EpisodeResult, state: dict[str, Any]) -> None:
        _finish_episode(self.episode, result, state)
        if self.defer_finish:
            self.session.flush()
        else:
            self._flush()


# ---- the loop -----------------------------------------------------------------------------------


@dataclass
class EpisodeResult:
    terminal_state: TerminalState
    reason: str  # the fulfilling call, the hand-off tool, the end_episode reason or the cap
    llm_calls: int
    steps: int
    tokens_in: int
    tokens_out: int
    actions: list[mock_actions.MockAction] = field(default_factory=list)
    escalations: list[escalation.EscalationRecord] = field(default_factory=list)
    approvals: list[ApprovalOutcome] = field(default_factory=list)  # accepted and refused
    truncations: int = 0
    invalid_outputs: int = 0

    def summary(self) -> dict[str, Any]:
        return {
            "terminal_state": self.terminal_state,
            "reason": self.reason,
            "llm_calls": self.llm_calls,
            "steps": self.steps,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "actions": [asdict(a) for a in self.actions],
            "escalations": [asdict(e) for e in self.escalations],
            "approvals": [a.model_dump(mode="json") for a in self.approvals],
            "truncations": self.truncations,
            "invalid_outputs": self.invalid_outputs,
        }


class EpisodeStopped(Exception):
    """The Console stopped the episode; it has no terminal state and is never scored."""


@dataclass
class _Ended(Exception):
    state: TerminalState
    reason: str


def _messages_hash(messages: list[Message]) -> str:
    blob = json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def _raw_text(raw: Any) -> str:
    return raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)


REASK = (
    "Your previous output could not be used ({error}). Answer again with exactly one JSON object "
    "that follows the schema."
)


@dataclass
class EpisodeRunner:
    """One episode. The tool side of each call goes through a ``ToolDispatcher``: by default the
    in-process one built from ``gate``, ``env`` and ``tools`` (they share one EpisodeState); A8
    passes an MCP dispatcher instead, whose server holds the gate and the state."""

    client: LLMClient
    request: Request
    prefix: str
    temperature: float
    seed: int | None
    trace: Trace
    counter: TokenCounter
    gate: Gate | None = None
    env: GateEnv | None = None
    tools: ToolEnvironment | None = None
    dispatcher: ToolDispatcher | None = None
    caps: Caps = field(default_factory=Caps)
    recorder: mock_actions.Recorder = field(default_factory=mock_actions.MemoryRecorder)
    retries: bool = True
    should_stop: Callable[[], bool] = lambda: False  # Console "Stop" (raises EpisodeStopped)
    prefix_label: str = PREFIX_LABEL  # Exp 3: LIVE_LABEL

    def __post_init__(self) -> None:
        if self.dispatcher is None:
            assert self.gate is not None and self.env is not None and self.tools is not None, (
                "give a dispatcher, or the gate and both environments"
            )
            self.dispatcher = InProcessDispatcher(self.gate, self.env, self.tools, self.trace,
                                                  self.recorder, self.retries)  # fmt: skip
        self.turns: list[Turn] = []
        self.llm_calls = 0
        self.steps = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.result_parts: dict[str, list[Any]] = {"actions": [], "escalations": [],
                                                   "approvals": []}  # fmt: skip
        self.truncations = 0
        self.invalid_outputs = 0

    # -------------------------------------------------------------- LLM

    def _call(self, prompt: Prompt, *, reask: bool) -> ProposerResult:
        if self.llm_calls >= self.caps.max_llm_calls:
            raise _Ended("cap", "max_llm_calls")
        if self.tokens_in + prompt.tokens > self.caps.max_input_tokens:
            raise _Ended("cap", "max_input_tokens")
        if prompt.tokens > self.caps.prompt_limit:
            raise _Ended("cap", "prompt_limit")
        t0 = time.perf_counter()
        res = propose(self.client, prompt, temperature=self.temperature, seed=self.seed)
        ms = round((time.perf_counter() - t0) * 1000, 3)
        self.llm_calls += 1
        self.tokens_in += res.usage.prompt_tokens
        self.tokens_out += res.usage.completion_tokens
        self.trace.step(
            "llm",
            {
                "call": self.llm_calls,
                "reask": reask,
                "proposal": res.proposal.model_dump(mode="json") if res.proposal else None,
                "raw": None if res.proposal else res.raw,
                "error": res.error,
                "usage": {
                    "prompt_tokens": res.usage.prompt_tokens,
                    "completion_tokens": res.usage.completion_tokens,
                },
                "prompt_tokens": prompt.tokens,
                "prompt_hash": _messages_hash(prompt.messages),
                "truncations": prompt.truncations,
                "temperature": self.temperature,
                "seed": self.seed,
            },
            ms,
        )
        return res

    def _next_proposal(self) -> Proposal | None:
        """A proposal, re-asking once; None when the output stayed unusable."""
        prompt = build_messages(
            self.prefix,
            self.request.text,
            self.turns,
            self.counter,
            self.caps.prompt_limit,
            self.prefix_label,
        )
        self.truncations += len(prompt.truncations)
        res = self._call(prompt, reask=False)
        if res.proposal is not None:
            return res.proposal
        self.invalid_outputs += 1
        retry: list[Message] = [
            {"role": "assistant", "content": _raw_text(res.raw)},
            {"role": "user", "content": REASK.format(error=(res.error or "invalid")[:300])},
        ]
        msgs = prompt.messages + retry
        again = Prompt(msgs, sum(self.counter.count(m["content"]) for m in msgs),
                       prompt.truncations)  # fmt: skip
        res2 = self._call(again, reask=True)
        if res2.proposal is not None:
            return res2.proposal
        self.invalid_outputs += 1
        self.turns.append(Turn(proposal=_raw_text(res2.raw),
                               feedback="INVALID_OUTPUT: your output could not be used; no action "
                               "was taken."))  # fmt: skip
        return None

    # -------------------------------------------------------------- dispatch

    def _feedback(self, p: Proposal, text: str, *, gate: bool, ids: Sequence[int] = ()) -> None:
        self.trace.step("feedback", {"text": text, "gate": gate})
        self.turns.append(Turn(proposal=p.model_dump_json(), feedback=text,
                               returned_ids=sorted(ids), gate_feedback=gate))  # fmt: skip

    def _dispatch(self, p: Proposal) -> None:
        self.steps += 1
        if p.is_end:  # a control action: no tool and no gate check (A-23)
            reason = str(p.args.get("reason", ""))
            self.trace.step("tool", {"tool": p.tool, "control": True, "reason": reason})
            raise _Ended("ended", reason)
        assert self.dispatcher is not None
        request = CallRequest(
            tool=p.tool,
            args=dict(p.args),
            cited=list(p.cited),
            technique_id=p.technique_id,
            thought=p.thought,
        )
        out = self.dispatcher.call(request)
        self.result_parts["approvals"] += [ApprovalOutcome.model_validate(a)
                                           for a in out.approvals]  # fmt: skip
        if out.executed is not None:
            ex = out.executed
            action = mock_actions.MockAction(
                ex["tool"], dict(ex["args"]), tuple(ex["cited"]), ex["gate_decision_id"]
            )
            self.result_parts["actions"].append(action)
            if fulfils(self.request, p.tool, p.args):
                raise _Ended("fulfilled", ex["call_key"])
        if out.handoff is not None:
            self.result_parts["escalations"].append(escalation.EscalationRecord(**out.handoff))
            raise _Ended("handed_off", p.tool)
        if out.gate_feedback:
            return self._feedback(p, out.feedback, gate=True)
        self.turns.append(Turn(proposal=p.model_dump_json(), feedback=out.feedback,
                               returned_ids=list(out.returned_ids)))  # fmt: skip

    # -------------------------------------------------------------- run

    def run(self) -> EpisodeResult:
        try:
            while True:
                if self.should_stop():
                    raise EpisodeStopped(f"stopped after {self.steps} steps")
                if self.steps >= self.caps.max_steps:
                    raise _Ended("cap", "max_steps")
                proposal = self._next_proposal()
                if proposal is None:
                    self.steps += 1
                    continue
                self._dispatch(proposal)
        except _Ended as end:
            result = EpisodeResult(
                terminal_state=end.state,
                reason=end.reason,
                llm_calls=self.llm_calls,
                steps=self.steps,
                tokens_in=self.tokens_in,
                tokens_out=self.tokens_out,
                actions=self.result_parts["actions"],
                escalations=self.result_parts["escalations"],
                approvals=self.result_parts["approvals"],
                truncations=self.truncations,
                invalid_outputs=self.invalid_outputs,
            )
        assert self.dispatcher is not None
        self.trace.finish(result, self.dispatcher.state())
        return result
