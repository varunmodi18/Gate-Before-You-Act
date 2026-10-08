"""Host names seen in a window (plan §D.1 step 4): ``Hostname`` or ``Computer``, as written."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from gbya.data.fieldmap import host_of


def host_counts(events: Iterable[dict[str, Any]]) -> Counter[str]:
    return Counter(h for e in events if (h := host_of(e)) is not None)


def hosts_by_frequency(counts: Counter[str]) -> list[str]:
    """Most frequent first; ties broken by name, so the order is deterministic."""
    return [h for h, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
