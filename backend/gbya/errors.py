"""Domain exceptions, mapped to the API error envelope of plan §F.6."""

from __future__ import annotations

from typing import Any


class GbyaError(Exception):
    """Base class. Subclasses set ``code`` and ``http_status``."""

    code: str = "INTERNAL_ERROR"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}
        if code is not None:
            self.code = code

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "hint": self.hint,
                "details": self.details,
            }
        }


class BadRequest(GbyaError):
    code = "BAD_REQUEST"
    http_status = 400


class NotFound(GbyaError):
    code = "NOT_FOUND"
    http_status = 404


class Conflict(GbyaError):
    code = "CONFLICT"
    http_status = 409


class Unprocessable(GbyaError):
    code = "UNPROCESSABLE"
    http_status = 422


class ModelUnavailable(GbyaError):
    code = "MODEL_UNAVAILABLE"
    http_status = 503


class ModelOutputInvalid(GbyaError):
    code = "MODEL_OUTPUT_INVALID"
    http_status = 502
