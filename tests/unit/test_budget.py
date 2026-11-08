"""T5.5: the budget monitor's projection (plan T5.5, §I.6). The F1 offer on a real run is tested in
tests/integration/test_exp2_runner.py."""

from __future__ import annotations

import re

import pytest

from gbya.config import REPO_ROOT, Settings
from gbya.experiments.runner import project


def test_projection_from_the_measured_pace() -> None:
    p = project(elapsed_s=3600, done=10, total=100, spent_other_h=5.0, limit_h=81.0)
    assert p is not None
    assert (p.elapsed_h, p.run_h, p.total_h, p.over) == (1.0, 10.0, 15.0, False)


def test_projection_over_the_budget() -> None:
    p = project(elapsed_s=36_000, done=10, total=100, spent_other_h=0.0, limit_h=81.0)
    assert p is not None and p.run_h == 100.0 and p.over


@pytest.mark.parametrize(("spent", "over"), [(71.0, False), (71.0001, True)])
def test_the_limit_itself_is_within_budget(spent: float, over: bool) -> None:
    p = project(elapsed_s=3600, done=1, total=10, spent_other_h=spent, limit_h=81.0)
    assert p is not None and p.total_h == pytest.approx(spent + 10) and p.over is over


def test_no_projection_before_a_unit_has_finished() -> None:
    assert project(elapsed_s=500, done=0, total=10, spent_other_h=0, limit_h=81) is None


def test_configured_limit_is_the_documented_t8_0_figure() -> None:
    """The configured GPU budget (``config/app.yaml``) is the measured T8.0 figure that
    ``docs/compute_budget.md`` documents (1.5x allowance included), set on purpose by the team lead
    (2026-11-08). The document keeps the proposal's 81.0 h as the planning figure."""
    text = (REPO_ROOT / "docs" / "compute_budget.md").read_text()
    m = re.search(r"\*\*([\d.]+) h\*\* with it \(planning figure ([\d.]+) h", text)
    assert m is not None, "docs/compute_budget.md no longer states the T8.0 figure"
    assert Settings(_env_file=None).gpu_budget_hours == float(m.group(1))  # type: ignore[call-arg]
    assert float(m.group(2)) == 81.0
