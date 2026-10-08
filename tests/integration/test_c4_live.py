"""T3.3 live smoke: G3 on the hand-made E1 with the real model, index and tokenizer.

Needs `make model-up` and `make index`; run with `uv run pytest -m gpu -s`. Prints the verdict;
asserts only that C4 ran and its output parsed (no assertion on content, plan T3.3).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gbya.cases.handmade import load_fixture
from gbya.config import REPO_ROOT
from gbya.context.models import TrustedContext
from gbya.data.catalogue import parse_metadata
from gbya.data.normalise import normalise_window
from gbya.experiments.exp1 import Exp1Case, Package, decide
from gbya.gate import verifier as vf
from gbya.gate.config import load_configs
from gbya.gate.gate import Gate
from gbya.llm.factory import _profile
from gbya.llm.live import LiveLLMClient
from gbya.llm.tokens import ModelTokenizer, model_tokenizer_path
from gbya.policy.engine import PolicyEngine, load_evidence_requirements
from gbya.retrieval import index as ix

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"


@pytest.mark.gpu
def test_live_g3_on_handmade_e1(tmp_path: Path) -> None:
    duck = tmp_path / "mini.duckdb"
    normalise_window(
        parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI), MINI, duck
    )
    data = load_fixture()
    raw = next(c for c in data["cases"] if c["id"] == "hm:E1")
    pkg = raw["package"]
    case = Exp1Case(
        case_id="hm:E1", db_path=duck,
        context=TrustedContext.model_validate(data["scenario"]["trusted_context"]),
        package=Package(pkg["tool"], pkg["args"], tuple(pkg["cited"]), pkg["technique_claimed"],
                        pkg["rationale"]),
    )  # fmt: skip
    index = ix.load(REPO_ROOT / "data" / "index")
    client = LiveLLMClient("http://127.0.0.1:8001/v1", str(_profile()["served_model_name"]))
    reqs = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
    verifier = vf.LLMVerifier(client, "standard", reqs, vf.live_retriever(index))
    gate = Gate(load_configs()["G3"], PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml"),
                verifier)  # fmt: skip
    counter = ModelTokenizer(model_tokenizer_path())
    decision, ms = decide(case, gate, counter)
    vc = decision.verifier_call
    assert vc is not None, "C4 was not reached"
    print(f"\nG3 on hm:E1: gate verdict={decision.verdict.value}; C4 output={vc.raw}; "
          f"tokens in/out={vc.tokens_in}/{vc.tokens_out}; C4 {vc.ms} ms; decision {ms} ms; "
          f"prompt tokens (manifest)={vc.manifest['tokens']['total']}")  # fmt: skip
    user = (vc.messages or [{}, {}])[1].get("content", "")
    print(f"ticket block: {user[user.index('CHANGE_TICKETS'):].splitlines()[0]!r}; "
          f"model ticket_scope={vc.output.ticket_scope if vc.output else None}; "
          f"code ticket_scope={vc.ticket_scope_code}")  # fmt: skip
    assert vc.output is not None, f"output did not parse: {vc.error}"
