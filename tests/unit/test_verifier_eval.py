"""T3.5: Exp 1V diagnostic accuracy and gate-path counts on hand-computed rows (§D.7.2)."""

from __future__ import annotations

from gbya.scoring.verifier_eval import CaseLabel, EvalRow, GateRow, verifier_report

LABELS = {
    "s:E1": CaseLabel("E1", "SUPPORTS"),
    "s:E3": CaseLabel("E3", "INSUFFICIENT"),
    "s:E5": CaseLabel("E5", "CONTRADICTED"),
    "s:X": CaseLabel("E2", None),  # unlabelled
}


def test_diagnostic_accuracy_counts_parse_errors_as_wrong() -> None:
    evals = [
        EvalRow("s:E1", "standard", 1, "SUPPORTS"),  # exact ✓ binary ✓
        EvalRow("s:E3", "standard", 1, "CONTRADICTED"),  # exact ✗ binary ✓ (both "not SUPPORTS")
        EvalRow("s:E5", "standard", 1, None),  # parse error: exact ✗ binary ✓ (not SUPPORTS)
        EvalRow("s:E1", "standard", 2, "INSUFFICIENT"),  # exact ✗ binary ✗
        EvalRow("s:X", "standard", 1, "SUPPORTS"),  # unlabelled: excluded
    ]
    r = verifier_report(evals, [], LABELS)
    std = r["diagnostic"]["standard"]
    assert (std["n"], std["exact_correct"], std["binary_correct"]) == (4, 1, 3)
    assert std["exact_accuracy"] == 0.25 and std["binary_accuracy"] == 0.75
    assert std["parse_errors"] == 1 and std["confusion"]["CONTRADICTED"]["PARSE_ERROR"] == 1
    assert (
        std["by_case_variant"]["E1"]["n"] == 2
        and std["by_case_variant"]["E1"]["exact_accuracy"] == 0.5
    )
    assert std["by_run"]["2"]["n"] == 1 and r["unlabelled_cases"] == ["s:X"]


def test_gate_path_counts_only_packages_that_reached_c4() -> None:
    gates = [
        GateRow("s:E3", "G3", 1, False, None),  # C3 stopped it
        GateRow("s:E3", "A2", 1, True, "INSUFFICIENT"),
        GateRow("s:E1", "G3", 1, True, "SUPPORTS"),
        GateRow("s:E1", "A2", 1, True, None),  # reached, unparseable
    ]
    r = verifier_report([], gates, LABELS)["gate_path"]
    assert (r["G3"]["packages"], r["G3"]["reached_c4"]) == (2, 1)
    assert r["G3"]["by_case_variant"]["E3"] == {
        "packages": 1, "reached_c4": 0,
        "conditional": {"n": 0, "exact_correct": 0, "binary_correct": 0, "exact_accuracy": None,
                        "binary_accuracy": None},
    }  # fmt: skip
    assert r["A2"]["conditional"]["n"] == 2 and r["A2"]["conditional"]["exact_accuracy"] == 0.5
