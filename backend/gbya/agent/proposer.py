"""The proposer: system prompt, output schema, message history and one LLM call (plan §D.10, T5.1).

**Output** (schema-constrained): ``{"thought", "tool", "args", "cited", "technique_id"}``. The
schema is an ``anyOf`` with one object per tool (``tool`` a constant, ``args`` typed for that
tool), plus the control action ``end_episode`` (A-23). Strings and arrays are bounded so a
generation cannot run to ``max_tokens`` (T0.3: typed args and bounded arrays are the levers for the
≥ 99 % schema-valid acceptance check). ``cited`` stays top-level and is merged into the arguments
of the tools that take citations (state-changing tools, ``request_approval``, ``draft_report``).

**Messages:** the system prompt (``prompts/proposer.md`` plus the tool catalogue); one user message
with the frozen prefix (Exp 2) and then the analyst request; then the turns: the proposal as the
assistant message and the tool result or gate feedback as the next user message.

**History policy** (§D.10.3), when the prompt would exceed 7,200 tokens: keep the system prompt,
the prefix and request, and the two most recent turns in full; replace older tool results by a
one-line stub listing the record IDs they returned (the registry is unaffected); never drop the
latest gate feedback; log every truncation. Token counts use the model's own tokenizer.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gbya.errors import ModelOutputInvalid
from gbya.llm.client import LLMClient
from gbya.llm.schemas import JsonSchema, Message, Usage
from gbya.llm.tokens import TokenCounter
from gbya.tools.names import ALL_TOOLS, TAKES_CITED
from gbya.tools.registry import DESCRIPTIONS, AnalystTopic, IncidentType
from gbya.tools.typed import ACCOUNT_PATTERN, HOSTNAME_PATTERN

PROMPT_PATH = Path(__file__).with_name("prompts") / "proposer.md"
END_EPISODE = "end_episode"
END_REASONS = ("awaiting_approval", "handed_off", "cannot_proceed")
MAX_TOKENS = 400  # proposer output (§D.10.3)
PROMPT_LIMIT = 7200  # proposer prompt (§D.10.3)
MAX_CITED = 16  # above the gate's 8 so over-citing stays expressible, bounded against loops
THOUGHT_CHARS = 480  # ≈ 80 words; with SQL_CHARS a valid output fits 400 tokens
SQL_CHARS = 600
TECHNIQUE = r"^T[0-9]{4}(\.[0-9]{3})?$"
# Record IDs are bounded too: without a maximum, a digit loop can run to max_tokens (T5.1 pilot;
# the largest window has 79,896 events).
MAX_RECORD_ID = 9_999_999

_HOST = {"type": "string", "pattern": HOSTNAME_PATTERN}
_ACCOUNT = {"type": "string", "pattern": ACCOUNT_PATTERN}
_PID = {"type": "integer", "minimum": 1, "maximum": 4294967295}  # 32-bit PIDs
_IP = {"type": "string", "minLength": 2, "maxLength": 45}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


TARGET_SCHEMAS: dict[str, dict[str, Any]] = {
    "isolate_host": _obj({"host": _HOST}),
    "kill_process": _obj({"host": _HOST, "pid": _PID}),
    "disable_account": _obj({"account": _ACCOUNT}),
    "block_ip": _obj({"ip": _IP}),
}
ARG_SCHEMAS: dict[str, dict[str, Any]] = {
    "sql_query": _obj({"sql": {"type": "string", "minLength": 1, "maxLength": SQL_CHARS}}),
    "get_context": _obj(
        {
            "section": {
                "enum": ["assets", "identities", "network", "approval_script", "change_tickets"]
            }
        }
    ),
    "request_approval": _obj(
        {
            "action": {
                "anyOf": [
                    _obj({"tool": {"const": t}, "args": s}) for t, s in TARGET_SCHEMAS.items()
                ]
            }
        }
    ),
    "ask_analyst": _obj(
        {
            "topic": {"enum": list(get_args(AnalystTopic))},
            "note": {"type": "string", "maxLength": 280},
        }
    ),
    "draft_report": _obj(
        {
            "incident_type": {"enum": list(get_args(IncidentType))},
            "note": {"type": "string", "maxLength": 300},
        }
    ),
    **TARGET_SCHEMAS,
    END_EPISODE: _obj({"reason": {"enum": list(END_REASONS)}}),
}


def proposer_schema() -> JsonSchema:
    def variant(tool: str) -> dict[str, Any]:
        return _obj(
            {
                "thought": {"type": "string", "maxLength": THOUGHT_CHARS},
                "tool": {"const": tool},
                "args": ARG_SCHEMAS[tool],
                "cited": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": MAX_RECORD_ID},
                    "maxItems": MAX_CITED,
                },
                "technique_id": {
                    "anyOf": [{"type": "string", "pattern": TECHNIQUE}, {"type": "null"}]
                },
            }
        )

    return {"anyOf": [variant(t) for t in (*ALL_TOOLS, END_EPISODE)]}


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    thought: str = ""
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    cited: list[int] = Field(default_factory=list)
    technique_id: str | None = None

    @property
    def is_end(self) -> bool:
        return self.tool == END_EPISODE

    def call_args(self) -> dict[str, Any]:
        """The arguments the tool layer and gate receive (``cited`` merged where it belongs)."""
        if self.tool in TAKES_CITED:
            return {**self.args, "cited": list(self.cited)}
        return dict(self.args)


def _catalogue() -> str:
    lines = []
    for tool in (*ALL_TOOLS, END_EPISODE):
        args = json.dumps(ARG_SCHEMAS[tool]["properties"], separators=(",", ":"))
        desc = DESCRIPTIONS.get(tool, "End the episode with a reason (control action, not a tool).")
        cites = " Cites records." if tool in TAKES_CITED and "cite" not in desc.lower() else ""
        lines.append(f"- {tool} {args} — {desc}{cites}")
    return "\n".join(lines)


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").rstrip() + "\n" + _catalogue()


# ---- messages and the history policy ------------------------------------------------------------


@dataclass
class Turn:
    """One step as the model sees it: its proposal and the feedback that followed."""

    proposal: str  # the assistant message (the proposal JSON)
    feedback: str  # tool result or gate feedback (the next user message)
    returned_ids: list[int] = field(default_factory=list)  # record IDs a tool result returned
    gate_feedback: bool = False


@dataclass
class Prompt:
    messages: list[Message]
    tokens: int
    truncations: list[dict[str, Any]]


PREFIX_LABEL = "INVESTIGATION SO FAR (scripted prefix)"  # Exp 2's frozen prefix
LIVE_LABEL = "INVESTIGATION SO FAR (your live investigation)"  # Exp 3's handover


def opening(prefix: str, request: str, label: str = PREFIX_LABEL) -> str:
    parts = []
    if prefix:
        parts.append(f"{label}\n" + prefix.strip())
    parts.append("ANALYST REQUEST\n" + request.strip())
    return "\n\n".join(parts)


def stub(turn: Turn) -> str:
    ids = ", ".join(str(i) for i in turn.returned_ids) or "none"
    return f"[earlier tool result omitted to fit the context; it returned record_ids: {ids}]"


def build_messages(
    prefix: str,
    request: str,
    turns: Sequence[Turn],
    counter: TokenCounter,
    limit: int = PROMPT_LIMIT,
    prefix_label: str = PREFIX_LABEL,
) -> Prompt:
    """Messages for the next proposal, reduced to ``limit`` tokens by the history policy."""
    system = system_prompt()
    head: list[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": opening(prefix, request, prefix_label)},
    ]
    feedback = [t.feedback for t in turns]
    latest_gate = max((i for i, t in enumerate(turns) if t.gate_feedback), default=None)
    truncations: list[dict[str, Any]] = []

    def assemble() -> list[Message]:
        msgs = list(head)
        for t, fb in zip(turns, feedback, strict=True):
            msgs += [{"role": "assistant", "content": t.proposal}, {"role": "user", "content": fb}]
        return msgs

    def size(msgs: list[Message]) -> int:
        return sum(counter.count(m["content"]) for m in msgs)

    msgs = assemble()
    protected = set(range(max(0, len(turns) - 2), len(turns)))  # two most recent turns
    if latest_gate is not None:
        protected.add(latest_gate)
    for i in range(len(turns)):  # oldest first
        if size(msgs) <= limit:
            break
        if i in protected or turns[i].gate_feedback:
            continue
        before = counter.count(feedback[i])
        feedback[i] = stub(turns[i])
        truncations.append(
            {"turn": i, "tokens_before": before, "tokens_after": counter.count(feedback[i])}
        )
        msgs = assemble()
    return Prompt(msgs, size(msgs), truncations)


# ---- the call -----------------------------------------------------------------------------------


@dataclass
class ProposerResult:
    proposal: Proposal | None
    usage: Usage
    raw: Any = None
    error: str | None = None


def propose(
    client: LLMClient, prompt: Prompt, *, temperature: float, seed: int | None
) -> ProposerResult:
    """One schema-constrained call; an unusable output is returned as an error (the episode loop
    re-asks once, §D.10.1)."""
    try:
        obj, usage = client.chat_json(
            prompt.messages,
            proposer_schema(),
            temperature=temperature,
            seed=seed,
            max_tokens=MAX_TOKENS,
        )
    except ModelOutputInvalid as exc:
        used = exc.details.get("usage") or {}
        return ProposerResult(
            None,
            Usage(
                prompt_tokens=int(used.get("prompt_tokens", 0)),
                completion_tokens=int(used.get("completion_tokens", 0)),
            ),
            exc.details.get("raw"),
            exc.details.get("error", exc.message),
        )
    try:
        return ProposerResult(Proposal.model_validate(obj), usage, obj)
    except ValidationError as exc:
        return ProposerResult(None, usage, obj, str(exc))
