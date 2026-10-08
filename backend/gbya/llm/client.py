"""The ``LLMClient`` protocol and its errors (plan §C.3, T0.4).

Every implementation returns the parsed JSON object and the token usage of the call. Output that
does not parse or does not match the schema raises ``ModelOutputInvalid``; how that counts
(re-ask, rejection) is decided by the caller per §D.6 / §D.10, never by the client.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from gbya.errors import GbyaError, ModelOutputInvalid, ModelUnavailable
from gbya.llm.schemas import JsonSchema, Message, Usage

__all__ = [
    "LLMClient",
    "ModelOutputInvalid",
    "ModelUnavailable",
    "ReplayMiss",
    "validate_output",
]


class ReplayMiss(GbyaError):
    code = "REPLAY_MISS"
    http_status = 503


@runtime_checkable
class LLMClient(Protocol):
    #: Usage summed over every call made through this client.
    total_usage: Usage

    def chat_json(
        self,
        messages: list[Message],
        schema: JsonSchema,
        *,
        temperature: float,
        seed: int | None,
        max_tokens: int,
    ) -> tuple[dict[str, Any], Usage]: ...


def validate_output(text: str, schema: JsonSchema, usage: Usage) -> dict[str, Any]:
    """Parse ``text`` as JSON and validate it against ``schema``."""
    import json

    import jsonschema

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelOutputInvalid(
            "Model output is not valid JSON",
            details={"raw": text[:2000], "error": str(exc), "usage": usage.model_dump()},
        ) from exc
    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as exc:
        raise ModelOutputInvalid(
            "Model output does not match the schema",
            details={"raw": text[:2000], "error": exc.message, "usage": usage.model_dump()},
        ) from exc
    if not isinstance(obj, dict):
        raise ModelOutputInvalid("Model output is not a JSON object", details={"raw": text[:2000]})
    return obj
