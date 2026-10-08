"""T1.6: seeded, stratified split; determinism; exact counts; groups never straddle splits."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gbya.data.split import (
    TARGETS,
    SplitError,
    WindowInfo,
    assign_splits,
    tactic_coverage,
    to_json,
)

TACTICS = ["TA0002", "TA0003", "TA0005", "TA0006", "TA0007", "TA0008"]


def _windows(n: int = 99, pairs: int = 5, ineligible: int = 0) -> list[WindowInfo]:
    out = []
    for i in range(n):
        wid = f"SDWIN-{i:03d}"
        group = f"SDWIN-{i - 1:03d}" if 0 < i <= 2 * pairs and i % 2 == 1 else wid
        out.append(
            WindowInfo(
                id=wid,
                dedup_group=group,
                primary_tactic=TACTICS[i % len(TACTICS)],
                eligible=i >= ineligible,
                techniques=(f"T{1000 + i}",),
            )
        )
    return out


def test_exact_counts_and_unused() -> None:
    res = assign_splits(_windows())
    counts = Counter(res.assignment.values())
    assert counts["dev"] == 10 and counts["test"] == 40 and counts["e2e"] == 12
    assert counts["unused"] == 99 - 62
    assert res.replacement_order == [
        w for w in res.replacement_order if res.assignment[w] == "unused"
    ]
    assert sorted(res.replacement_order) == res.members("unused")


def test_deterministic_for_a_seed_and_seed_sensitive() -> None:
    a, b = assign_splits(_windows(), seed=2026), assign_splits(_windows(), seed=2026)
    assert a.assignment == b.assignment and a.replacement_order == b.replacement_order
    assert to_json(a, _windows(), {}) == to_json(b, _windows(), {})
    c = assign_splits(_windows(), seed=7)
    assert c.assignment != a.assignment


def test_input_order_does_not_matter() -> None:
    w = _windows()
    assert assign_splits(w).assignment == assign_splits(list(reversed(w))).assignment


def test_groups_stay_together() -> None:
    res = assign_splits(_windows(pairs=20))
    for w in _windows(pairs=20):
        assert res.assignment[w.id] == res.assignment[w.dedup_group]


def test_ineligible_windows_are_never_assigned() -> None:
    res = assign_splits(_windows(n=99, ineligible=5))
    assert all(res.assignment[f"SDWIN-{i:03d}"] == "ineligible" for i in range(5))
    assert Counter(res.assignment.values())["ineligible"] == 5


def test_too_few_windows_is_q2() -> None:
    with pytest.raises(SplitError, match="Q-2"):
        assign_splits(_windows(n=61))


def test_tactics_are_spread_over_splits() -> None:
    res = assign_splits(_windows())
    cov = tactic_coverage(res, _windows())
    assert len(cov["test"]) == len(TACTICS)  # every tactic present in test
    assert len(cov["e2e"]) == len(TACTICS) and len(cov["dev"]) == len(TACTICS)
    # each split's per-tactic counts are within 1 of the proportional share
    for split, target in TARGETS.items():
        share = target / len(TACTICS)
        assert all(abs(n - share) <= 1 for n in cov[split].values()), (split, cov[split])


def test_json_document() -> None:
    doc = json.loads(to_json(assign_splits(_windows()), _windows(), {"otrf_commit": "d9d40ef"}))
    assert doc["targets"] == TARGETS and doc["seed"] == 2026 and doc["otrf_commit"] == "d9d40ef"
    assert {len(doc["splits"][s]) for s in ("dev", "test", "e2e")} == {10, 40, 12}
    assert doc["windows"]["SDWIN-000"]["primary_tactic"] == "TA0002"


@given(
    n=st.integers(min_value=62, max_value=110),
    pairs=st.integers(min_value=0, max_value=25),
    seed=st.integers(min_value=0, max_value=10_000),
)
@settings(max_examples=60, deadline=None)
def test_property_no_group_straddles_and_counts_exact(n: int, pairs: int, seed: int) -> None:
    windows = _windows(n=n, pairs=min(pairs, n // 2 - 1))
    try:
        res = assign_splits(windows, seed=seed)
    except SplitError:
        # only acceptable when 2-window groups make an exact fill impossible
        assert n - 62 < 2
        return
    by_group: dict[str, set[str]] = {}
    for w in windows:
        by_group.setdefault(w.dedup_group, set()).add(res.assignment[w.id])
    assert all(len(s) == 1 for s in by_group.values())
    counts = Counter(res.assignment.values())
    assert (counts["dev"], counts["test"], counts["e2e"]) == (10, 40, 12)
