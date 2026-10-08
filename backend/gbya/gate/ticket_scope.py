"""Ticket scope computed in code (team decision after M3; diagnostic only).

The verifier reports ``ticket_scope`` for the cited record that best meets the requirement. This
module computes the same four matches deterministically over **every cited record** against the
**approved** target tickets, with the definitions the verifier is given:

* host — the record's host is the ticket's host (case-insensitive);
* account — the record's acting user is the ticket's account (``DOMAIN\\`` stripped, lower-case);
* command — the record's ``command_line`` matches the ticket's ``command_pattern``
  (``re.search``; a record without a command line never matches);
* time — the record's timestamp (naive UTC) lies within [start, end].

``applies`` is true when some (record, approved ticket) pair matches all four; the reported
matches are that pair's, otherwise the pair with the most matches (first record, then first
ticket, on ties). No approved ticket → ``applies`` and all four are false.

It is stored next to the model's output and compared with it in Exp 1V. **It never changes the
verdict**: the gate decides on the verifier's verdict alone. The case validator (T4.4, check b)
uses ``record_matches`` for its per-event ticket-scope check.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from gbya.context.models import ChangeTicket, account_key, host_key
from gbya.data.fieldmap import acting_user_column
from gbya.gate.evidence import CitedRecord

FIELDS = ("host", "account", "command", "time")


def _naive_utc(t: datetime) -> datetime:
    return t.astimezone(UTC).replace(tzinfo=None) if t.tzinfo else t


def acting_user(rec: CitedRecord) -> str | None:
    if rec.table is None:
        return None
    v = rec.values.get(acting_user_column(rec.table, int(rec.values.get("event_id") or 0)))
    return account_key(str(v)) if v else None


def record_matches(
    rec: CitedRecord, ticket: ChangeTicket, command: str | None = None
) -> dict[str, bool]:
    """``command`` overrides the record's own command line (the validator passes the acting
    process's command line, found through the PID lineage, for records that have none)."""
    host = rec.host
    user = acting_user(rec)
    cmd = (
        command if command is not None else (rec.values.get("command_line") if rec.table else None)
    )
    ts = rec.ts
    return {
        "host": host is not None and host_key(host) == host_key(ticket.host),
        "account": user is not None and user == account_key(ticket.account),
        "command": bool(cmd) and re.search(ticket.command_pattern, str(cmd)) is not None,
        "time": ts is not None
        and _naive_utc(ticket.start) <= _naive_utc(ts) <= _naive_utc(ticket.end),
    }


def code_ticket_scope(
    records: Sequence[CitedRecord], tickets: Sequence[ChangeTicket]
) -> dict[str, Any]:
    approved = [t for t in tickets if t.approved]
    best: tuple[int, dict[str, bool], str | None, int | None] = (
        -1, dict.fromkeys(FIELDS, False), None, None,
    )  # fmt: skip
    for rec in records:
        for t in approved:
            m = record_matches(rec, t)
            score = sum(m.values())
            if score > best[0]:
                best = (score, m, t.id, rec.record_id)
    _, matches, ticket_id, record_id = best
    return {
        "applies": all(matches.values()) and ticket_id is not None,
        "matches": matches,
        "ticket_id": ticket_id,
        "record_id": record_id,
    }


def agreement(model: dict[str, Any] | None, code: dict[str, Any] | None) -> dict[str, bool] | None:
    """Field-by-field agreement of the model's ticket_scope with the code's (None if either is
    missing)."""
    if not model or not code:
        return None
    mm = model.get("matches") or {}
    out = {"applies": bool(model.get("applies")) == bool(code["applies"])}
    out |= {f: bool(mm.get(f)) == bool(code["matches"][f]) for f in FIELDS}
    return out
