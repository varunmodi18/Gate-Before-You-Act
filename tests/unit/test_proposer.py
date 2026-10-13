"""T5.1: proposer prompt, output schema, history policy and the call (plan §D.10, §D.10.3)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from gbya.agent import proposer as pp
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.tokens import ApproxCounter, ModelTokenizer, model_tokenizer_path
from gbya.tools.registry import ARG_MODELS

SNAP = Path(__file__).resolve().parents[1] / "snapshots" / "proposer"
SCHEMA = pp.proposer_schema()

SAMPLES: dict[str, dict[str, Any]] = {
    "sql_query": {"sql": "SELECT * FROM process_access LIMIT 20"},
    "get_context": {"section": "change_tickets"},
    "request_approval": {
        "action": {"tool": "isolate_host", "args": {"host": "WKSTN-01.lab.local"}}
    },
    "ask_analyst": {"topic": "insufficient_evidence", "note": "need more logs"},
    "draft_report": {"incident_type": "suspicious_activity_unconfirmed", "note": ""},
    "isolate_host": {"host": "WKSTN-01.lab.local"},
    "kill_process": {"host": "WKSTN-01.lab.local", "pid": 4100},
    "disable_account": {"account": "a.mehta"},
    "block_ip": {"ip": "203.0.113.7"},
    "end_episode": {"reason": "awaiting_approval"},
}


def proposal(tool: str, **kw: Any) -> dict[str, Any]:
    return {"thought": "t", "tool": tool, "args": SAMPLES[tool],
            "cited": [5] if tool in pp.TAKES_CITED else [], "technique_id": None, **kw}  # fmt: skip


def test_snapshot_of_the_system_prompt_and_schema() -> None:
    text = pp.system_prompt() + "\n\n=== SCHEMA ===\n" + json.dumps(SCHEMA, indent=1) + "\n"
    path = SNAP / "proposer.txt"
    if os.environ.get("GBYA_UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), "proposer snapshot changed; review and regenerate"


@pytest.mark.skipif(not model_tokenizer_path().is_file(), reason="model tokenizer not downloaded")
def test_system_prompt_within_target() -> None:
    assert (
        ModelTokenizer(model_tokenizer_path()).count(pp.system_prompt()) <= 1800
    )  # §D.10.3 target


@pytest.mark.parametrize("tool", list(SAMPLES))
def test_schema_accepts_each_tool_and_the_args_match_the_tool_layer(tool: str) -> None:
    obj = proposal(tool, technique_id="T1003.001")
    jsonschema.validate(obj, SCHEMA)
    p = pp.Proposal.model_validate(obj)
    if tool != pp.END_EPISODE:
        ARG_MODELS[tool].model_validate(p.call_args())  # the tool layer accepts it as well
    assert ("cited" in p.call_args()) is (tool in pp.TAKES_CITED)


@pytest.mark.parametrize(
    "obj",
    [
        proposal("isolate_host", args={"host": "WKSTN-01", "pid": 4}),  # args of another tool
        proposal("kill_process", args={"host": "WKSTN-01", "pid": 0}),
        proposal("isolate_host", args={"host": "bad host; rm -rf"}),
        proposal("sql_query", args={"sql": "S" * (pp.SQL_CHARS + 1)}),
        proposal("isolate_host", cited=list(range(1, pp.MAX_CITED + 2))),
        proposal("isolate_host", technique_id="credential dumping"),
        proposal("isolate_host", thought="x" * (pp.THOUGHT_CHARS + 1)),
        proposal("end_episode", args={"reason": "done"}),
        {**proposal("isolate_host"), "tool": "delete_logs"},  # no such tool
        {**proposal("isolate_host"), "extra": 1},
        proposal("request_approval", args={"action": {"tool": "sql_query", "args": {"sql": "x"}}}),
    ],
)  # fmt: skip
def test_schema_rejects_malformed_proposals(obj: dict[str, Any]) -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(obj, SCHEMA)


# ---- history policy -----------------------------------------------------------------------------


def turn(i: int, size: int, *, gate: bool = False) -> pp.Turn:
    return pp.Turn(proposal=json.dumps(proposal("sql_query")),
                   feedback=(f"GATE {i}: " if gate else f"RESULT {i}: ") + "x" * size,
                   returned_ids=[i * 10, i * 10 + 1], gate_feedback=gate)  # fmt: skip


def test_short_history_is_unchanged() -> None:
    p = pp.build_messages("prefix", "Contain host WKSTN-01.", [turn(1, 50)], ApproxCounter())
    assert p.truncations == [] and len(p.messages) == 4
    assert (
        p.messages[1]["content"].startswith("INVESTIGATION SO FAR")
        and "ANALYST REQUEST" in p.messages[1]["content"]
    )


def test_long_history_is_stubbed_but_keeps_recent_turns_and_the_latest_gate_feedback() -> None:
    counter = ApproxCounter()  # 3 chars per token
    turns = [turn(1, 4500), turn(2, 4500, gate=True), turn(3, 4500), turn(4, 4500), turn(5, 4500)]
    p = pp.build_messages("prefix", "Contain host WKSTN-01.", turns, counter, limit=7200)
    feedback = [m["content"] for m in p.messages if m["role"] == "user"][1:]
    assert feedback[0].startswith("[earlier tool result omitted") and "10, 11" in feedback[0]
    assert feedback[1].startswith("GATE 2")  # the latest gate feedback survives
    assert feedback[3].startswith("RESULT 4") and feedback[4].startswith("RESULT 5")  # last two
    assert [t["turn"] for t in p.truncations] == [0, 2] and p.tokens <= 7200
    assert p.messages[0]["content"] == pp.system_prompt()  # system prompt and request kept


def test_history_reports_when_it_cannot_fit() -> None:
    turns = [turn(1, 30000), turn(2, 30000)]  # the two most recent turns are never stubbed
    p = pp.build_messages("", "Contain host H.", turns, ApproxCounter(), limit=7200)
    assert p.tokens > 7200 and p.truncations == []


# ---- the call -----------------------------------------------------------------------------------------


def test_propose_parses_and_uses_the_decoding_parameters() -> None:
    client = FakeLLMClient([FakeRule([proposal("isolate_host", technique_id="T1003.001")])])
    prompt = pp.build_messages("", "Contain host WKSTN-01.", [], ApproxCounter())
    res = pp.propose(client, prompt, temperature=0.2, seed=11)
    assert res.proposal is not None and res.proposal.tool == "isolate_host" and res.error is None
    call = client.calls[0]
    assert (call.temperature, call.seed, call.max_tokens) == (0.2, 11, 400)
    assert call.schema == SCHEMA


def test_propose_reports_unusable_output() -> None:
    client = FakeLLMClient([FakeRule(['{"thought": "cut off'])])
    prompt = pp.build_messages("", "Contain host WKSTN-01.", [], ApproxCounter())
    res = pp.propose(client, prompt, temperature=0.2, seed=11)
    assert res.proposal is None and res.error and res.raw == '{"thought": "cut off'
