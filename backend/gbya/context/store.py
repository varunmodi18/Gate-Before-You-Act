"""Loading, hashing and the ``get_context`` view of trusted context (plan §D.4, T2.1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from gbya.context.models import SECTIONS, Section, TrustedContext


def load_context(path: Path) -> TrustedContext:
    return TrustedContext.model_validate_json(path.read_text())


def canonical_json(ctx: TrustedContext) -> str:
    """Sorted keys, no whitespace: the same context always gives the same text."""
    return json.dumps(
        ctx.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def context_hash(ctx: TrustedContext) -> str:
    """SHA-256 of the canonical JSON; equal across E1-E5 of a scenario (validator check a)."""
    return hashlib.sha256(canonical_json(ctx).encode()).hexdigest()


def get_context(ctx: TrustedContext, section: Section) -> Any:
    """The ``get_context`` tool's answer: one section of the trusted context, as JSON data."""
    if section not in SECTIONS:
        raise ValueError(f"unknown context section {section!r}; one of {SECTIONS}")
    return ctx.model_dump(mode="json")[section]
