"""Escalation tools ``ask_analyst`` and ``draft_report`` (plan §D.5, A-5, T2.3).

Their ``note`` is shown only to humans, never parsed or executed, length-limited by the argument
model, and flagged when it copies 30 or more characters verbatim from log data the model was shown
(so planted log text cannot be passed on unnoticed). ``draft_report`` is never sent anywhere.
``request_approval`` is handled by the approval service with the gate (§D.6.2a).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from gbya.tools.registry import AskAnalystArgs, DraftReportArgs

VERBATIM_MIN = 30


def copies_log_text(note: str, shown: Sequence[str], min_len: int = VERBATIM_MIN) -> bool:
    """True if any ``min_len``-character run of ``note`` occurs in text shown from the logs."""
    if len(note) < min_len or not shown:
        return False
    haystack = "\n".join(shown)
    return any(note[i : i + min_len] in haystack for i in range(len(note) - min_len + 1))


@dataclass(frozen=True)
class EscalationRecord:
    tool: str
    topic_or_type: str
    cited: tuple[int, ...]
    note: str
    note_copies_log_text: bool
    message: str  # returned to the model


def ask_analyst(args: AskAnalystArgs, shown: Sequence[str]) -> EscalationRecord:
    return EscalationRecord(
        tool="ask_analyst",
        topic_or_type=args.topic,
        cited=(),
        note=args.note,
        note_copies_log_text=copies_log_text(args.note, shown),
        message=f"Handed off to the analyst (topic: {args.topic}).",
    )


def draft_report(args: DraftReportArgs, shown: Sequence[str]) -> EscalationRecord:
    return EscalationRecord(
        tool="draft_report",
        topic_or_type=args.incident_type,
        cited=tuple(args.cited),
        note=args.note,
        note_copies_log_text=copies_log_text(args.note, shown),
        message=f"Report drafted for human review (type: {args.incident_type}); not sent.",
    )
