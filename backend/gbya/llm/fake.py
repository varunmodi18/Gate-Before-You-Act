"""Scripted fake client for tests (plan T0.4). Needs no model server and no GPU.

Responses are chosen by rules, tried in order:

* ``FakeRule(prompt_hash=...)`` matches the exact prompt hash (``gbya.llm.schemas.prompt_hash``);
* ``FakeRule(pattern=...)`` matches a regular expression searched in the concatenated messages;
* a rule with neither matches any call (a default).

A rule's ``responses`` are returned in turn; the last one repeats once the list is used up.
A response that is a ``str`` is returned as raw model text, so tests can exercise invalid output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from gbya.llm.client import validate_output
from gbya.llm.schemas import JsonSchema, Message, Usage, prompt_hash


class FakeNoMatch(AssertionError):
    """No scripted rule matched the call (a test-setup error)."""


@dataclass
class FakeRule:
    responses: list[dict[str, Any] | str]
    prompt_hash: str | None = None
    pattern: str | None = None
    _next: int = 0

    def matches(self, phash: str, text: str) -> bool:
        if self.prompt_hash is not None and self.prompt_hash != phash:
            return False
        return not (self.pattern is not None and re.search(self.pattern, text) is None)

    def take(self) -> dict[str, Any] | str:
        resp = self.responses[min(self._next, len(self.responses) - 1)]
        self._next += 1
        return resp


@dataclass
class FakeCall:
    messages: list[Message]
    schema: JsonSchema
    temperature: float
    seed: int | None
    max_tokens: int
    prompt_hash: str


@dataclass
class FakeLLMClient:
    rules: list[FakeRule] = field(default_factory=list)
    calls: list[FakeCall] = field(default_factory=list)
    total_usage: Usage = field(default_factory=Usage)

    def chat_json(
        self,
        messages: list[Message],
        schema: JsonSchema,
        *,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Usage]:
        phash = prompt_hash(messages, schema)
        self.calls.append(FakeCall(messages, schema, temperature, seed, max_tokens, phash))
        text = "\n".join(m["content"] for m in messages)
        for rule in self.rules:
            if rule.matches(phash, text):
                resp = rule.take()
                out = resp if isinstance(resp, str) else json.dumps(resp)
                # Approximate token counts: whitespace-separated words.
                usage = Usage(prompt_tokens=len(text.split()), completion_tokens=len(out.split()))
                self.total_usage = self.total_usage + usage
                return validate_output(out, schema, usage), usage
        raise FakeNoMatch(f"No FakeRule matched prompt {phash[:12]}")
