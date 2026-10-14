"""T8.0 prepared: the token audit (real prompts, scripted agent) and the regenerated budget run
on the mini fixture; the audit is refused before 10 dev scenarios are adjudicated."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from gbya.llm.tokens import ApproxCounter
from tests.integration.test_exp2_runner import build

REPO = Path(__file__).resolve().parents[2]


def script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_audit_and_budget(tmp_path: Path) -> None:
    audit_mod, budget = script("token_audit"), script("budget")
    settings = build(tmp_path)
    with pytest.raises(SystemExit, match="10 adjudicated dev scenarios"):
        audit_mod.audit(settings, ApproxCounter())
    a = audit_mod.audit(settings, ApproxCounter(), force=True, scenarios=["mini"])
    assert a["cases"] == 7 and a["tokenizer"] == "approx-test-only"
    turns = a["proposer"]["by_turn"]
    assert set(turns) == {"1", "2", "3", "4"} and turns["4"]["mean"] > turns["1"]["mean"]  # history
    assert {"standard", "rationale", "none"} <= set(a["verifier"]["by_variant"])
    assert "rerank" in a["verifier"]["skipped"]  # no reranker installed in tests
    assert set(a["investigator"]["by_step"]) == {"hypothesise", "sqlgen", "technique_verifier"}
    assert a["checks"] == {"proposer_within_limit": True, "verifier_within_budget": True,
                           "investigator_within_context": True}  # fmt: skip
    b = budget.compute(a, {"in": 1229.2, "out": 55.4}, None, None)
    assert [(r["experiment"], r["call"]) for r in b["rows"]] == [
        ("Exp 1", "verifier"), ("Exp 2", "proposer"), ("Exp 2", "verifier"),
        ("Exp 3", "investigator"), ("Exp 3", "proposer"), ("Exp 3", "verifier")]  # fmt: skip
    exp1 = b["rows"][0]
    assert exp1["calls"] == 280 * 4 * 3  # cases x verifier variants x runs
    assert b["hours_with_allowance"] == pytest.approx(b["hours_before_allowance"] * 1.5, abs=0.11)
    assert b["assumptions"]["source"] == {"exp2": "proposal", "exp3": "proposal"}
    md = budget.markdown(a, b)
    assert "| Exp 2 | proposer |" in md and "planning figure 81.0 h" in md
    cfg = tmp_path / "app.yaml"
    cfg.write_text("env: dev\n")
    budget.apply_limit(12.5, cfg)
    assert "gpu_budget_hours: 12.5" in cfg.read_text()
