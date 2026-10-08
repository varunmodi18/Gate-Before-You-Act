"""T4.8: Cohen's κ and Jaccard on hand-computed examples (A-11)."""

from __future__ import annotations

import json

import pytest

from gbya.analysis.agreement import cohen_kappa, items, jaccard, summarise


def test_kappa_hand_computed_2x2() -> None:
    # A yes/B yes 20, yes/no 5, no/yes 10, no/no 15 → p_o = 0.70, p_e = 0.5·0.6 + 0.5·0.4 = 0.50
    pairs = [("y", "y")] * 20 + [("y", "n")] * 5 + [("n", "y")] * 10 + [("n", "n")] * 15
    assert cohen_kappa(pairs) == pytest.approx(0.4)


def test_kappa_edge_cases() -> None:
    assert cohen_kappa([]) is None
    assert cohen_kappa([("a", "a")] * 5) is None  # no variation: undefined, not perfect
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    assert cohen_kappa([("a", "b"), ("b", "a")]) == pytest.approx(-1.0)


def test_jaccard() -> None:
    assert jaccard([4, 5, 7], [4, 5]) == pytest.approx(2 / 3)
    assert jaccard([1], [2]) == 0.0 and jaccard([], []) is None


def test_items_pair_outcomes_actions_and_e1_evidence() -> None:
    iso = {"tool": "isolate_host", "args": {"host": "H"}}
    a = {"E1": {"correct_outcome": "safe_completion", "permitted": [{**iso, "requires_approval": False, "fulfils": True}],
                "prohibited": [{"tool": "disable_account", "args": {"account": "*"}}],
                "evidence_counterfactual": [4, 5, 7]},
         "E3": {"correct_outcome": "justified_escalation", "prohibited": [iso]}}  # fmt: skip
    b = {"E1": {"correct_outcome": "safe_completion", "permitted": [{**iso, "requires_approval": False, "fulfils": True}],
                "evidence_counterfactual": [4, 5]},
         "E3": {"correct_outcome": "refusal", "prohibited": [iso]}}  # fmt: skip
    it = items(a, b)
    assert it["outcome"] == [
        ["E1", "safe_completion", "safe_completion"],
        ["E3", "justified_escalation", "refusal"],
    ]
    labels = {(v, json.loads(k)["tool"]): (x, y) for v, k, x, y in it["actions"]}
    assert labels[("E1", "isolate_host")] == ("permitted", "permitted")
    assert labels[("E1", "disable_account")] == ("prohibited", "unlisted")
    assert labels[("E3", "isolate_host")] == ("prohibited", "prohibited")
    assert it["jaccard_e1"] == pytest.approx(2 / 3)
    s = summarise([it])
    assert (s["outcome_items"], s["action_items"], s["outcome_agreement"]) == (2, 3, 0.5)
    assert s["action_agreement"] == pytest.approx(2 / 3)
