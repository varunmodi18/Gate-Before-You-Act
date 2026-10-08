"""Rendering of query results shown to the model (plan §D.5.1 "Execution").

Rows become JSON objects with ``record_id`` first, inside ``<<UNTRUSTED_LOG_DATA>> …
<</UNTRUSTED_LOG_DATA>>``. Each field is shortened to 200 characters and the rendered result is
capped at 1,500 tokens (model tokenizer). Rows that do not fit are **dropped entirely**, reported
in a note outside the untrusted block, and are not registered (Draft 8 wording of §D.5.1). A row
with a shortened field is still shown and registered: the gate never uses the shown text.

This display cut applies only to query results for the proposer; verifier evidence is never cut
(§D.7.1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from gbya.llm.tokens import TokenCounter

FIELD_CAP = 200
TOKEN_CAP = 1500
OPEN, CLOSE = "<<UNTRUSTED_LOG_DATA>>", "<</UNTRUSTED_LOG_DATA>>"


@dataclass(frozen=True)
class Rendered:
    text: str
    rows_shown: int
    rows_total: int
    fields_cut: int
    tokens: int


def _value(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def _cut(v: Any) -> tuple[Any, bool]:
    if isinstance(v, str) and len(v) > FIELD_CAP:
        return f"{v[:FIELD_CAP]}…[cut: {len(v)} chars]", True
    return v, False


def _keys(columns: Sequence[str]) -> list[str]:
    """Unique keys (a join can return two ``record_id`` columns)."""
    seen: dict[str, int] = {}
    out = []
    for c in columns:
        seen[c] = seen.get(c, 0) + 1
        out.append(c if seen[c] == 1 else f"{c}#{seen[c]}")
    return out


def render_rows(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
    counter: TokenCounter,
    token_cap: int = TOKEN_CAP,
) -> Rendered:
    keys = _keys(columns)
    order = sorted(range(len(keys)), key=lambda i: (keys[i] != "record_id", i))
    lines: list[str] = []
    cut_total = 0
    shown = 0
    used = counter.count(f"{OPEN}\n{CLOSE}")
    for row in rows:
        obj = {}
        cut_here = 0
        for i in order:
            val, was_cut = _cut(_value(row[i]))
            cut_here += was_cut
            obj[keys[i]] = val
        line = json.dumps(obj, ensure_ascii=False)
        cost = counter.count(line + "\n")
        if used + cost > token_cap:
            break
        lines.append(line)
        used += cost
        cut_total += cut_here
        shown += 1
    text = OPEN + "\n" + "".join(f"{line}\n" for line in lines) + CLOSE
    if shown < len(rows):
        text += (
            f"\n[{len(rows) - shown} more row(s) not shown: result limit {token_cap} tokens. "
            "Narrow the query.]"
        )
    if not rows:
        text += "\n[no rows]"
    return Rendered(text, shown, len(rows), cut_total, counter.count(text))
