"""Retrieval query text, identical for every mode (plan §D.3 "Query construction", A-17).

The query is built from the proposed tool name, its evidence requirement, and for each cited
record **re-read from the canonical database** (``gbya.gate.evidence.read_cited``): table name,
event ID, image, parent image, command line, target image or object, and granted access where
present. Only values are used (no field labels). The tool arguments, the agent's
``technique_claimed`` and ``technique_gold`` never enter the query.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from gbya.gate.evidence import CitedRecord

# (role, candidate columns in order); the first non-empty column is used.
QUERY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("event_id", ("event_id",)),
    ("image", ("image", "source_image", "process_name")),
    ("parent_image", ("parent_image",)),
    ("command_line", ("command_line",)),
    ("target", ("target_image", "target_object", "target_filename")),
    ("granted_access", ("granted_access",)),
)


def _record_line(rec: CitedRecord) -> str:
    parts = [rec.table or "raw_events"]
    values = rec.values if rec.table else {"event_id": rec.raw.get("EventID")}
    for _, columns in QUERY_FIELDS:
        for col in columns:
            v = values.get(col)
            if v is not None and str(v) != "":
                parts.append(str(v))
                break
    return " ".join(parts)


def build_query(tool: str, records: Sequence[CitedRecord], requirements: Mapping[str, str]) -> str:
    """``records`` in citation order, as returned by ``read_cited``; unknown tools have no
    requirement text."""
    lines = [tool.replace("_", " ")]
    if req := requirements.get(tool):
        lines.append(" ".join(req.split()))
    lines += [_record_line(r) for r in records]
    return "\n".join(lines)
