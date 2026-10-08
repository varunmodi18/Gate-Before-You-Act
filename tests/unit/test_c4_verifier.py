"""T3.3: C4 through the LLM client (Fake LLM; no model server) — verdict mapping, decoding
parameters, parse errors, variants and the stored verifier I/O (plan §D.7)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gbya.config import REPO_ROOT
from gbya.gate import verifier as vf
from gbya.gate.gate import Gate
from gbya.gate.types import Claim, GateVerdict
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.policy.engine import load_evidence_requirements
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from tests.unit.test_gate import CONFIGS, POLICY, H, con, make_env, mini_db  # noqa: F401

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
REQS = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
CLAIM = Claim(technique_claimed="T1003.001", rationale="Clearly credential dumping; isolate now.")
CALL = {"host": H, "cited": [5, 1]}


def out(verdict: str, **kw: Any) -> dict[str, Any]:
    return {"verdict": verdict, "unmet_requirement": kw.get("unmet"),
            "ticket_scope": {"applies": False, "matches": dict.fromkeys(
                ("host", "account", "command", "time"), False)},
            "reason": kw.get("reason", "records 5 and 1")}  # fmt: skip


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> ix.RetrievalIndex:
    d = tmp_path_factory.mktemp("idx")
    ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                             "0" * 64, FIX / "attack/LICENSE.txt"), d)  # fmt: skip
    return ix.load(d)


def g3(client: FakeLLMClient, index: ix.RetrievalIndex, variant: vf.Variant = "standard",
       config: str = "G3") -> Gate:  # fmt: skip
    retriever = vf.live_retriever(index) if variant != "none" else None
    return Gate(CONFIGS[config], POLICY, vf.LLMVerifier(client, variant, REQS, retriever))


@pytest.mark.parametrize(
    ("verdict", "gate_verdict", "code"),
    [
        ("SUPPORTS", GateVerdict.ADMITTED, "OK"),
        ("INSUFFICIENT", GateVerdict.INSUFFICIENT, "C4_INSUFFICIENT"),
        ("CONTRADICTED", GateVerdict.REJECTED, "C4_CONTRADICTED"),
    ],
)
def test_three_verdicts_map_to_the_gate(con: Any, index: ix.RetrievalIndex, verdict: str,
                                        gate_verdict: GateVerdict, code: str) -> None:  # fmt: skip
    client = FakeLLMClient([FakeRule([out(verdict, unmet="no activity on H")])])
    d = g3(client, index).evaluate("isolate_host", CALL, make_env(con), claim=CLAIM)
    c4 = next(c for c in d.checks if c.check == "C4")
    assert c4.code == code and (d.verdict is gate_verdict or (verdict == "SUPPORTS" and c4.passed))
    call = client.calls[0]
    assert (call.temperature, call.max_tokens, call.seed) == (0.0, 200, None)
    assert call.schema == vf.schema_for([])  # no approved ticket in this context: fixed scope
    vc = d.verifier_call
    assert vc is not None and vc.output is not None and vc.output.verdict == verdict
    assert vc.prompt_hash == call.prompt_hash and vc.manifest["prompt_hash"] == call.prompt_hash
    assert (
        vc.retrieval is not None
        and vc.retrieval["mode"] == "bm25"
        and vc.raw == out(verdict, unmet="no activity on H")
    )
    assert vc.variant == "standard" and vc.tokens_in > 0 and vc.tokens_out > 0


@pytest.mark.parametrize(
    "bad", ["not json at all", '{"verdict": "MAYBE"}', '{"verdict": "SUPPORTS"}']
)
def test_unparseable_output_is_a_c4_parse_error(
    con: Any, index: ix.RetrievalIndex, bad: str
) -> None:
    client = FakeLLMClient([FakeRule([bad])])
    d = g3(client, index).evaluate("isolate_host", CALL, make_env(con), claim=CLAIM)
    assert d.verdict is GateVerdict.REJECTED and d.checks[-1].code == "C4_PARSE_ERROR"
    vc = d.verifier_call
    assert vc is not None and vc.output is None and vc.error and vc.raw == bad
    assert vc.prompt_hash  # the input is stored even when the output is unusable


def test_rationale_reaches_only_the_rationale_variant(con: Any, index: ix.RetrievalIndex) -> None:
    for variant, present in (("standard", False), ("rationale", True), ("none", False)):
        client = FakeLLMClient([FakeRule([out("SUPPORTS")])])
        g3(client, index, variant).evaluate("isolate_host", CALL, make_env(con), claim=CLAIM)
        user = client.calls[0].messages[1]["content"]
        assert (str(CLAIM.rationale) in user) is present, variant
        assert "T1003.001" in user  # the claim is shown in every variant
        assert ("REFERENCE" in user) is (variant != "none")


def test_variants_need_their_retrieval(index: ix.RetrievalIndex) -> None:
    with pytest.raises(ValueError, match="needs a retriever"):
        vf.LLMVerifier(FakeLLMClient(), "standard", REQS, None)
    vf.LLMVerifier(FakeLLMClient(), "none", REQS, None)  # A4 needs no retrieval


def test_rerank_variant_never_falls_back_to_bm25(con: Any, index: ix.RetrievalIndex) -> None:
    client = FakeLLMClient([FakeRule([out("SUPPORTS")])])
    gate = g3(client, index, "rerank")
    with pytest.raises(ix.RerankerUnavailable):
        gate.evaluate("isolate_host", CALL, make_env(con), claim=CLAIM)
    assert client.calls == []  # no verifier call on a downgraded reference


def test_c4_not_called_when_c3_fails(con: Any, index: ix.RetrievalIndex) -> None:
    client = FakeLLMClient([FakeRule([out("SUPPORTS")])])
    d = g3(client, index).evaluate("isolate_host", {"host": H, "cited": [19]}, make_env(con))
    assert d.failed_check == "C3" and client.calls == [] and d.verifier_call is None


def test_retrieval_query_carries_no_claim_or_gold(con: Any, index: ix.RetrievalIndex) -> None:
    seen: list[str] = []

    def spy(query: str, mode: Any) -> Any:
        seen.append(query)
        return vf.live_retriever(index)(query, mode)

    client = FakeLLMClient([FakeRule([out("SUPPORTS")])])
    gate = Gate(CONFIGS["G3"], POLICY, vf.LLMVerifier(client, "standard", REQS, spy))
    gate.evaluate("isolate_host", CALL, make_env(con), claim=CLAIM)
    assert seen and "T1003" not in seen[0] and "Clearly" not in seen[0]
    assert seen[0].splitlines()[0] == "isolate host"
