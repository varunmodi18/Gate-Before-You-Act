"""Gate data types (plan §D.6.1). ``CheckResult`` now (T2.4); verdicts and decisions in T2.5."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CheckName = Literal["C1", "C2", "C3", "C4", "C5", "C6"]


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: CheckName
    passed: bool
    code: str  # e.g. "C3_TARGET_NOT_IN_EVIDENCE"; "OK" when passed
    message: str  # shown to the agent (feedback) and the UI
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0
