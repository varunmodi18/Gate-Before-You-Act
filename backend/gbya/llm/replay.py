"""Replay ("cassette") client for demo resilience (plan T0.4, FR-26).

A cassette is a JSON-lines file; each line records one call:
``{"prompt_hash", "params", "output", "usage"}``. Lookup is by (prompt hash, params); a call
that was not recorded raises ``ReplayMiss``. Replay output is never used for research runs:
runs created with it are flagged ``replay=true`` and excluded from analysis (§F.1, NFR-12).

``RecordingLLMClient`` wraps a live client and appends every successful call to a cassette
(used by T8.2 to record the demo).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gbya.llm.client import LLMClient, ReplayMiss
from gbya.llm.schemas import CallParams, JsonSchema, Message, Usage, params_key, prompt_hash


def _key(phash: str, params: CallParams) -> str:
    return f"{phash}|{params_key(params)}"


class ReplayLLMClient:
    def __init__(self, cassette: Path) -> None:
        self.cassette = cassette
        self.total_usage = Usage()
        self._entries: dict[str, dict[str, Any]] = {}
        for line in cassette.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                params = CallParams.model_validate(entry["params"])
                self._entries[_key(entry["prompt_hash"], params)] = entry

    def chat_json(
        self,
        messages: list[Message],
        schema: JsonSchema,
        *,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Usage]:
        params = CallParams(temperature=temperature, seed=seed, max_tokens=max_tokens)
        phash = prompt_hash(messages, schema)
        entry = self._entries.get(_key(phash, params))
        if entry is None:
            raise ReplayMiss(
                "No recorded response for this prompt and parameters",
                hint="Replay only covers the recorded demo script; use the live model",
                details={"prompt_hash": phash, "params": params.model_dump()},
            )
        usage = Usage.model_validate(entry["usage"])
        self.total_usage = self.total_usage + usage
        output: dict[str, Any] = entry["output"]
        return output, usage


class RecordingLLMClient:
    """Pass-through client that appends each successful call to a cassette."""

    def __init__(self, inner: LLMClient, cassette: Path) -> None:
        self.inner = inner
        self.cassette = cassette
        self.total_usage = Usage()

    def chat_json(
        self,
        messages: list[Message],
        schema: JsonSchema,
        *,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Usage]:
        output, usage = self.inner.chat_json(
            messages, schema, temperature=temperature, seed=seed, max_tokens=max_tokens
        )
        params = CallParams(temperature=temperature, seed=seed, max_tokens=max_tokens)
        entry = {
            "prompt_hash": prompt_hash(messages, schema),
            "params": params.model_dump(),
            "output": output,
            "usage": usage.model_dump(),
        }
        self.cassette.parent.mkdir(parents=True, exist_ok=True)
        with self.cassette.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
        self.total_usage = self.total_usage + usage
        return output, usage
