"""Mock state-changing actions (plan §D.5.2a, FR-08). Nothing is ever executed.

An admitted call is recorded (tool, normalised arguments, cited ids, gate decision id) and gets a
deterministic success message. Recording goes through a ``Recorder``; the database recorder that
writes ``tool_calls`` rows arrives with the episode loop (T5.2).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class MockAction:
    tool: str
    args: dict[str, Any]
    cited: tuple[int, ...]
    gate_decision_id: int | None

    @property
    def message(self) -> str:
        shown = json.dumps(self.args, sort_keys=True)
        return f"[mock action recorded] {self.tool} {shown}: no real system was changed."


class Recorder(Protocol):
    def record(self, action: MockAction) -> None: ...


@dataclass
class MemoryRecorder:
    actions: list[MockAction] = field(default_factory=list)

    def record(self, action: MockAction) -> None:
        self.actions.append(action)


def execute(
    tool: str,
    args: dict[str, Any],
    cited: list[int],
    recorder: Recorder,
    gate_decision_id: int | None = None,
) -> MockAction:
    """Record an admitted state-changing call. Callers reach this only through the gate."""
    action = MockAction(tool, dict(args), tuple(cited), gate_decision_id)
    recorder.record(action)
    return action
