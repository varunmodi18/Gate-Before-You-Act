"""Per-episode environment state shared by the tool layer and the gate (plan §D.5.2, §D.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ApprovalState = Literal["none", "granted", "denied", "pending"]


@dataclass
class EpisodeState:
    #: record ids the agent has actually seen through direct base-table projections (§D.5.2)
    retrieved: set[int] = field(default_factory=set)
    #: approval state per normalised call key (§D.6.2a; written only by the approval service)
    approvals: dict[str, ApprovalState] = field(default_factory=dict)
    approval_requests: int = 0
    #: recovery queries left after an INSUFFICIENT verdict (§D.6.2a; set from the configuration)
    recovery_queries_left: int = 0
    log_deletion_attempts: int = 0
    unknown_tool_calls: int = 0
    #: untrusted text shown to the model (tool results), to flag verbatim copies in notes (§D.5)
    shown_untrusted: list[str] = field(default_factory=list)
