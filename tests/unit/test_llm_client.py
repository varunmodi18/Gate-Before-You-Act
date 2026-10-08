"""T0.4: LLM client abstraction — Fake, Live (mock transport) and Replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from gbya.llm.client import LLMClient, ModelOutputInvalid, ModelUnavailable, ReplayMiss
from gbya.llm.fake import FakeLLMClient, FakeNoMatch, FakeRule
from gbya.llm.live import LiveLLMClient
from gbya.llm.replay import RecordingLLMClient, ReplayLLMClient
from gbya.llm.schemas import Usage, prompt_hash

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"verdict": {"enum": ["SUPPORTS", "INSUFFICIENT", "CONTRADICTED"]}},
    "required": ["verdict"],
    "additionalProperties": False,
}
MSGS = [{"role": "system", "content": "verifier"}, {"role": "user", "content": "record 41"}]
KW: dict[str, Any] = {"temperature": 0.0, "seed": 1, "max_tokens": 200}


# ---------- Fake ----------


def test_fake_matches_pattern_and_records_calls() -> None:
    fake = FakeLLMClient([FakeRule([{"verdict": "SUPPORTS"}], pattern=r"record 41")])
    out, usage = fake.chat_json(MSGS, SCHEMA, **KW)
    assert out == {"verdict": "SUPPORTS"}
    assert usage.prompt_tokens > 0 and fake.total_usage == usage
    assert fake.calls[0].prompt_hash == prompt_hash(MSGS, SCHEMA)
    assert fake.calls[0].temperature == 0.0


def test_fake_matches_exact_prompt_hash_before_default() -> None:
    fake = FakeLLMClient(
        [
            FakeRule([{"verdict": "CONTRADICTED"}], prompt_hash=prompt_hash(MSGS, SCHEMA)),
            FakeRule([{"verdict": "INSUFFICIENT"}]),
        ]
    )
    assert fake.chat_json(MSGS, SCHEMA, **KW)[0]["verdict"] == "CONTRADICTED"
    other = [{"role": "user", "content": "something else"}]
    assert fake.chat_json(other, SCHEMA, **KW)[0]["verdict"] == "INSUFFICIENT"


def test_fake_sequence_then_repeat_last() -> None:
    fake = FakeLLMClient([FakeRule([{"verdict": "INSUFFICIENT"}, {"verdict": "SUPPORTS"}])])
    got = [fake.chat_json(MSGS, SCHEMA, **KW)[0]["verdict"] for _ in range(3)]
    assert got == ["INSUFFICIENT", "SUPPORTS", "SUPPORTS"]


def test_fake_invalid_output_raises() -> None:
    fake = FakeLLMClient([FakeRule(["not json"]), FakeRule([{"verdict": "MAYBE"}])])
    with pytest.raises(ModelOutputInvalid):
        fake.chat_json(MSGS, SCHEMA, **KW)
    fake2 = FakeLLMClient([FakeRule([{"verdict": "MAYBE"}])])
    with pytest.raises(ModelOutputInvalid):
        fake2.chat_json(MSGS, SCHEMA, **KW)


def test_fake_no_match_is_a_setup_error() -> None:
    with pytest.raises(FakeNoMatch):
        FakeLLMClient([FakeRule([{"verdict": "SUPPORTS"}], pattern="nope")]).chat_json(
            MSGS, SCHEMA, **KW
        )


def test_clients_satisfy_protocol(tmp_path: Path) -> None:
    (tmp_path / "c.jsonl").write_text("")
    assert isinstance(FakeLLMClient(), LLMClient)
    assert isinstance(LiveLLMClient("http://x/v1", "m"), LLMClient)
    assert isinstance(ReplayLLMClient(tmp_path / "c.jsonl"), LLMClient)


# ---------- Live (mock transport, no network) ----------


def _ok(content: str, pt: int = 120, ct: int = 9) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct},
        },
    )


def _live(handler: Any, sleeps: list[float] | None = None) -> LiveLLMClient:
    record = sleeps if sleeps is not None else []
    return LiveLLMClient(
        "http://127.0.0.1:8001/v1",
        "qwen",
        transport=httpx.MockTransport(handler),
        sleep=record.append,
    )


def test_live_sends_json_schema_and_returns_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return _ok('{"verdict": "SUPPORTS"}')

    client = _live(handler)
    out, usage = client.chat_json(MSGS, SCHEMA, **KW)
    assert out == {"verdict": "SUPPORTS"}
    assert usage == Usage(prompt_tokens=120, completion_tokens=9)
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert (body["temperature"], body["seed"], body["max_tokens"]) == (0.0, 1, 200)
    client.chat_json(MSGS, SCHEMA, **KW)
    assert client.total_usage == Usage(prompt_tokens=240, completion_tokens=18)


def test_live_retries_5xx_with_backoff_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(502), _ok('{"verdict": "SUPPORTS"}')])
    sleeps: list[float] = []
    out, _ = _live(lambda _req: next(responses), sleeps).chat_json(MSGS, SCHEMA, **KW)
    assert out["verdict"] == "SUPPORTS"
    assert sleeps == [2.0, 8.0]


def test_live_retries_connection_errors_then_gives_up() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    sleeps: list[float] = []
    with pytest.raises(ModelUnavailable) as exc:
        _live(handler, sleeps).chat_json(MSGS, SCHEMA, **KW)
    assert sleeps == [2.0, 8.0, 30.0]
    assert exc.value.details["attempts"] == 4
    assert exc.value.http_status == 503


def test_live_does_not_retry_4xx() -> None:
    sleeps: list[float] = []
    with pytest.raises(ModelUnavailable):
        _live(lambda _req: httpx.Response(400, text="bad"), sleeps).chat_json(MSGS, SCHEMA, **KW)
    assert sleeps == []


def test_live_invalid_output_raises_with_usage() -> None:
    with pytest.raises(ModelOutputInvalid) as exc:
        _live(lambda _req: _ok('{"verdict": "PERHAPS"}')).chat_json(MSGS, SCHEMA, **KW)
    assert exc.value.details["usage"]["prompt_tokens"] == 120


# ---------- Replay ----------


def test_record_then_replay_round_trip(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.jsonl"
    fake = FakeLLMClient([FakeRule([{"verdict": "CONTRADICTED"}])])
    out, usage = RecordingLLMClient(fake, cassette).chat_json(MSGS, SCHEMA, **KW)

    replay = ReplayLLMClient(cassette)
    out2, usage2 = replay.chat_json(MSGS, SCHEMA, **KW)
    assert (out2, usage2) == (out, usage)


def test_replay_miss_on_other_prompt_or_params(tmp_path: Path) -> None:
    cassette = tmp_path / "demo.jsonl"
    fake = FakeLLMClient([FakeRule([{"verdict": "SUPPORTS"}])])
    RecordingLLMClient(fake, cassette).chat_json(MSGS, SCHEMA, **KW)
    replay = ReplayLLMClient(cassette)
    with pytest.raises(ReplayMiss):
        replay.chat_json([{"role": "user", "content": "other"}], SCHEMA, **KW)
    with pytest.raises(ReplayMiss):
        replay.chat_json(MSGS, SCHEMA, **{**KW, "temperature": 0.7})


# ---------- Live smoke test against the real model server ----------


@pytest.mark.gpu
def test_live_smoke_against_model_server() -> None:
    """Needs `make model-up`. Run with: uv run pytest -m gpu"""
    import yaml

    from gbya.config import REPO_ROOT

    profiles = yaml.safe_load((REPO_ROOT / "config" / "model_profiles.yaml").read_text())
    active = profiles["profiles"][profiles["default"]]
    client = LiveLLMClient("http://127.0.0.1:8001/v1", active["served_model_name"])
    msgs = [
        {"role": "system", "content": "You check evidence. Answer only with JSON."},
        {"role": "user", "content": "Record 41: lsass.exe accessed by outflank.exe. Verdict?"},
    ]
    out, usage = client.chat_json(msgs, SCHEMA, temperature=0.0, seed=1, max_tokens=50)
    assert out["verdict"] in {"SUPPORTS", "INSUFFICIENT", "CONTRADICTED"}
    assert usage.prompt_tokens > 0 and usage.completion_tokens > 0
