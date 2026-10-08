"""Live client for the local OpenAI-compatible model server (plan T0.4, §F.8).

Retries connection errors and 5xx responses with backoff of 2, 8 and 30 seconds (three retries
after the first attempt), then raises ``ModelUnavailable``. Structured output uses
``response_format: {"type": "json_schema"}``, the form confirmed against the server in T0.3.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from gbya.llm.client import ModelUnavailable, validate_output
from gbya.llm.schemas import JsonSchema, Message, Usage

DEFAULT_BACKOFF_S: tuple[float, ...] = (2.0, 8.0, 30.0)


class LiveLLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = 300.0,
        backoff_s: Sequence[float] = DEFAULT_BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.backoff_s = tuple(backoff_s)
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout_s, transport=transport)
        self.total_usage = Usage()

    def close(self) -> None:
        self._http.close()

    def _body(
        self,
        messages: list[Message],
        schema: JsonSchema,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": schema, "strict": True},
            },
        }
        if seed is not None:
            body["seed"] = seed
        return body

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error = ""
        for attempt in range(len(self.backoff_s) + 1):
            if attempt:
                self._sleep(self.backoff_s[attempt - 1])
            try:
                resp = self._http.post(f"{self.base_url}/chat/completions", json=body)
            except httpx.TransportError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code >= 500:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                continue
            if resp.status_code != 200:
                # A 4xx is a request problem; retrying the same request cannot help.
                raise ModelUnavailable(
                    f"Model server rejected the request (HTTP {resp.status_code})",
                    details={"body": resp.text[:1000]},
                )
            data: dict[str, Any] = resp.json()
            return data
        raise ModelUnavailable(
            "Model server unavailable after retries",
            hint="Start it with `make model-up`; check logs/model.log",
            details={"attempts": len(self.backoff_s) + 1, "last_error": last_error},
        )

    def chat_json(
        self,
        messages: list[Message],
        schema: JsonSchema,
        *,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Usage]:
        data = self._post(self._body(messages, schema, temperature, seed, max_tokens))
        raw_usage = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(raw_usage.get("prompt_tokens", 0)),
            completion_tokens=int(raw_usage.get("completion_tokens", 0)),
        )
        self.total_usage = self.total_usage + usage
        text = data["choices"][0]["message"].get("content") or ""
        return validate_output(text, schema, usage), usage
