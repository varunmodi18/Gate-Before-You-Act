"""Types shared by the LLM clients (plan T0.4)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}
JsonSchema = dict[str, Any]


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


class CallParams(BaseModel):
    temperature: float
    seed: int | None
    max_tokens: int


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def prompt_hash(messages: list[Message], schema: JsonSchema) -> str:
    """Stable hash of what the model sees: the messages and the output schema."""
    return hashlib.sha256(_canonical({"messages": messages, "schema": schema}).encode()).hexdigest()


def params_key(params: CallParams) -> str:
    return _canonical(params.model_dump())
