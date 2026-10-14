"""T8.5: the generated documents (``docs/api.md`` from the OpenAPI schema, ``docs/gate_contract.md``
from plan §D.6) are current. Regenerate with ``make docs``."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gen_docs", REPO / "scripts" / "gen_docs.py")
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def test_api_reference_is_current() -> None:
    assert (REPO / "docs" / "api.md").read_text() == gen.api_markdown(), "run make docs"


def test_gate_contract_is_current() -> None:
    text = (REPO / "docs" / "gate_contract.md").read_text()
    assert text == gen.gate_markdown(), "run make docs"
    assert "#### " not in text and "D.6.2a Approval contract" in text
