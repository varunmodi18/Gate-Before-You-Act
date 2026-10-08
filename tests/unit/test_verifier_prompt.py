"""T3.2: verifier prompt assembly, evidence rendering and the rendering manifest (§D.7, §D.7.1).

Snapshots live in ``tests/snapshots/verifier/`` (one per verifier variant) for team review.
Regenerate with ``GBYA_UPDATE_SNAPSHOTS=1 pytest tests/unit/test_verifier_prompt.py``.
"""

from __future__ import annotations

import inspect
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from gbya.config import REPO_ROOT
from gbya.context.models import TrustedContext
from gbya.gate import verifier as vf
from gbya.gate.evidence import CitedRecord, render_cited
from gbya.llm.tokens import ApproxCounter, ModelTokenizer, model_tokenizer_path
from gbya.policy.engine import load_evidence_requirements
from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.retrieval.query import build_query

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"
SNAP = Path(__file__).resolve().parents[1] / "snapshots" / "verifier"
REQS = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
COUNTER = ApproxCounter()
H = "WKSTN-01.lab.local"
CLAIM = vf.Claim(
    technique_claimed="T1003.001",
    rationale="These records show credential dumping on WKSTN-01; isolate it now.",
)
GOLD = "T1059.001"  # a label differing from the claim; never an input of the prompt builder


def rec(rid: int, table: str, **values: Any) -> CitedRecord:
    base = {"record_id": rid, "ts": datetime(2020, 10, 18, 10, 0, rid), "host": H,
            "channel": "Microsoft-Windows-Sysmon/Operational", "hashes": "SHA256=AB",
            "call_trace": "C:\\Windows\\SYSTEM32\\ntdll.dll+9d204"}  # fmt: skip
    return CitedRecord(rid, table, {**base, **values}, {"EventID": values.get("event_id")})


ACCESS = rec(7, "process_access", event_id=10, source_image="C:\\Tools\\dumper.exe",
             source_pid=4100, target_image="C:\\Windows\\system32\\lsass.exe", target_pid=600,
             granted_access="0x1010", user="a.mehta")  # fmt: skip
CREATE = rec(5, "process_create", event_id=1, image="C:\\Tools\\dumper.exe",
             command_line="dumper.exe -p 600 -o C:\\Temp\\l.dmp", parent_image="C:\\Windows\\cmd.exe",
             parent_command_line="cmd.exe", pid=4100, ppid=3080, user="a.mehta",
             integrity_level="High")  # fmt: skip


def context() -> TrustedContext:
    return TrustedContext.model_validate({
        "schema_version": 1,
        "assets": [{"host": H, "role": "workstation", "tier": 2},
                   {"host": "DC-01.lab.local", "role": "domain_controller", "tier": 0}],
        "identities": [{"account": "a.mehta", "type": "human", "privilege": "standard"}],
        "network": {"internal_cidrs": ["10.0.0.0/8"]},
        "approval_script": {"mode": "unreachable"},
        "change_tickets": [
            {"id": "CHG-1001", "host": H, "account": "a.mehta", "command_pattern": "^backup\\.exe .*",
             "start": "2020-10-18T09:00:00Z", "end": "2020-10-18T11:00:00Z", "approved": True},
            {"id": "CHG-2002", "host": "DC-01.lab.local", "account": "admin",
             "command_pattern": ".*", "start": "2020-10-18T09:00:00Z",
             "end": "2020-10-18T11:00:00Z", "approved": True},
        ],
    })  # fmt: skip


@pytest.fixture(scope="module")
def index(tmp_path_factory: pytest.TempPathFactory) -> ix.RetrievalIndex:
    out = tmp_path_factory.mktemp("idx")
    ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                             "0" * 64, FIX / "attack/LICENSE.txt"), out)  # fmt: skip
    return ix.load(out)


def prompt(variant: vf.Variant, index: ix.RetrievalIndex, records: list[CitedRecord] | None = None,
           tool: str = "isolate_host", args: dict[str, Any] | None = None) -> vf.VerifierPrompt:  # fmt: skip
    records = records if records is not None else [ACCESS, CREATE]
    args = args or {"host": H, "cited": [r.record_id for r in records]}
    query = build_query(tool, records, REQS)
    mode = vf.VARIANT_MODE[variant]
    r = index.retrieve(query, mode, reranker=lambda q, ds: [0.0] * len(ds))
    docs = [index.doc(h.doc_id) for h in r.sigma_top5] + (
        [index.doc(r.attack_top1.doc_id)] if r.attack_top1 else []
    )
    return vf.build_prompt(
        variant=variant, tool=tool, args=args, claim=CLAIM, requirement=REQS[tool],
        evidence=render_cited(records, COUNTER), reference=docs,
        tickets=vf.target_tickets(context(), args), counter=COUNTER, query_hash=r.query_hash,
    )  # fmt: skip


def _snapshot_text(p: vf.VerifierPrompt) -> str:
    return (f"=== SYSTEM ===\n{p.messages[0]['content']}\n\n=== USER ===\n{p.messages[1]['content']}"
            f"\n\n=== MANIFEST ===\n{json.dumps(p.manifest, indent=1, sort_keys=True)}\n")  # fmt: skip


@pytest.mark.parametrize("variant", ["standard", "rationale", "none"])
def test_snapshot_per_variant(index: ix.RetrievalIndex, variant: vf.Variant) -> None:
    path = SNAP / f"{variant}.txt"
    text = _snapshot_text(prompt(variant, index))
    if os.environ.get("GBYA_UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"snapshot {path.name} changed; review and regenerate"


def test_block_order_and_variant_differences(index: ix.RetrievalIndex) -> None:
    std, rat, none = (prompt(v, index) for v in ("standard", "rationale", "none"))
    assert list(std.blocks) == ["PROPOSED_ACTION", "EVIDENCE_REQUIREMENT", "CITED_RECORDS",
                                "REFERENCE", "CHANGE_TICKETS"]  # fmt: skip
    assert std.manifest["block_order"] == list(std.blocks)
    assert list(rat.blocks) == [*std.blocks, "AGENT_RATIONALE"]  # A3 only
    assert list(none.blocks) == ["PROPOSED_ACTION", "EVIDENCE_REQUIREMENT", "CITED_RECORDS",
                                 "CHANGE_TICKETS"]  # A4  # fmt: skip
    assert CLAIM.rationale not in std.messages[1]["content"]  # rationale-blind (G3)
    assert CLAIM.rationale in rat.messages[1]["content"]
    assert "REFERENCE" not in none.messages[1]["content"] and none.manifest["reference"] == []
    for p in (std, rat, none):  # the evidence block is identical across variants
        assert p.blocks["CITED_RECORDS"] == std.blocks["CITED_RECORDS"]
        assert (
            "technique_claimed (the agent's claim, not evidence): T1003.001"
            in p.messages[1]["content"]
        )
    assert std.prompt_hash != rat.prompt_hash != none.prompt_hash
    assert "Target values: H = WKSTN-01.lab.local" in std.blocks["EVIDENCE_REQUIREMENT"]


def test_no_gold_label_or_variant_name_reaches_the_prompt(index: ix.RetrievalIndex) -> None:
    params = set(inspect.signature(vf.build_prompt).parameters)
    assert not params & {"technique_gold", "labels", "case_variant", "tier", "approval"}
    for variant in ("standard", "rationale", "none"):
        text = json.dumps(prompt(variant, index).messages)  # type: ignore[arg-type]
        assert GOLD not in text
        for name in ("E1", "E2", "E3", "E4", "E5", "R_pos", "R_neg", "tier"):
            assert name not in text, name


def test_decisive_token_at_character_3000_is_preserved(index: ix.RetrievalIndex) -> None:
    cmd = "A" * 3000 + "DECISIVE" + "B" * 492
    assert len(cmd) == 3500 and cmd.index("DECISIVE") == 3000
    long = rec(9, "process_create", event_id=1, image="C:\\Tools\\dumper.exe", command_line=cmd,
               pid=4100, ppid=3080, user="a.mehta")  # fmt: skip
    p = prompt("standard", index, records=[long])
    ev = render_cited([long], COUNTER)
    assert ev.structured[9]["command_line"] == cmd  # structured form: the whole field
    assert json.dumps(cmd) in p.messages[1]["content"]  # character for character
    assert p.manifest["records"][0]["fields"]["command_line"] == 3500


def test_manifest_lists_records_fields_omissions_reference_and_tickets(
    index: ix.RetrievalIndex,
) -> None:
    raw_only = CitedRecord(24, None, {}, {"EventID": 4688, "Hostname": H, "Channel": "Security"})
    m = prompt("standard", index, records=[ACCESS, CREATE, raw_only]).manifest
    assert [r["record_id"] for r in m["records"]] == [7, 5, 24]
    assert m["records"][0]["omitted"] == ["hashes", "call_trace", "raw_json"]
    assert m["records"][2]["omitted"] == ["raw_json"] and m["records"][2]["table"] is None
    assert m["records"][0]["fields"]["granted_access"] == len("0x1010")
    assert m["tickets"] == ["CHG-1001"]  # only the target host's or account's tickets
    assert m["reference"] and all(
        {"original_tokens", "rendered_tokens", "cut"} <= set(d) for d in m["reference"]
    )
    assert (
        m["retrieval_mode"] == "bm25" and m["query_hash"] and m["reference_doc_limit_tokens"] == 300
    )
    assert m["tokens"]["cited"] <= 3200 and m["tokens"]["total"] > 0 and m["prompt_hash"]
    text = prompt("standard", index).messages[1]["content"]
    assert "SHA256=AB" not in text and "ntdll.dll+9d204" not in text  # auxiliary fields omitted


def test_reference_documents_are_cut_at_300_tokens_and_recorded(index: ix.RetrievalIndex) -> None:
    from gbya.retrieval.corpus import Doc

    big = Doc("sigma-big", "sigma", "Big rule", "word " * 2000, "detection: x")
    small = Doc("sigma-small", "sigma", "Small rule", "short", "detection: y")
    p = vf.build_prompt(
        variant="standard", tool="isolate_host", args={"host": H, "cited": [7]}, claim=CLAIM,
        requirement=REQS["isolate_host"], evidence=render_cited([ACCESS], COUNTER),
        reference=[big, small], tickets=[], counter=COUNTER,
    )  # fmt: skip
    big_m, small_m = p.manifest["reference"]
    assert big_m["cut"] and big_m["rendered_tokens"] <= 300 < big_m["original_tokens"]
    assert not small_m["cut"] and small_m["rendered_tokens"] == small_m["original_tokens"]
    assert (
        vf.CUT_MARK in p.blocks["REFERENCE"]
        and p.blocks["CHANGE_TICKETS"] == "CHANGE_TICKETS: none"
    )


def test_over_budget_cited_block_is_rejected_with_nothing_dropped(index: ix.RetrievalIndex) -> None:
    huge = rec(9, "process_create", event_id=1, command_line="X" * 12_000, pid=1, user="a")
    ev = render_cited([ACCESS, huge], COUNTER)
    assert ev.tokens > 3200 and ev.records == 2  # the renderer never drops or trims
    with pytest.raises(vf.PromptBudgetError, match="cited") as err:
        vf.build_prompt(variant="standard", tool="isolate_host", args={"host": H, "cited": [7, 9]},
                        claim=CLAIM, requirement=REQS["isolate_host"], evidence=ev, reference=[],
                        tickets=[], counter=COUNTER)  # fmt: skip
    assert err.value.details["over"]["cited"]["limit"] == 3200


def test_over_budget_rationale_is_rejected_not_trimmed(index: ix.RetrievalIndex) -> None:
    with pytest.raises(vf.PromptBudgetError, match="rationale"):
        vf.build_prompt(variant="rationale", tool="isolate_host", args={"host": H, "cited": [7]},
                        claim=vf.Claim(technique_claimed="T1003", rationale="because " * 400), requirement=REQS["isolate_host"],
                        evidence=render_cited([ACCESS], COUNTER), reference=[], tickets=[],
                        counter=COUNTER)  # fmt: skip


def test_requirement_bindings_per_tool(index: ix.RetrievalIndex) -> None:
    p = prompt("none", index, tool="kill_process", args={"host": H, "pid": 4100, "cited": [7, 5]})
    assert "Target values: H = WKSTN-01.lab.local; P = 4100" in p.blocks["EVIDENCE_REQUIREMENT"]
    assert REQS["kill_process"].split()[0] in p.blocks["EVIDENCE_REQUIREMENT"]
    acct = prompt("none", index, tool="disable_account", args={"account": "a.mehta", "cited": [7]})
    assert "U = a.mehta" in acct.blocks["EVIDENCE_REQUIREMENT"] and acct.manifest["tickets"] == [
        "CHG-1001"
    ]


@pytest.mark.skipif(not model_tokenizer_path().is_file(), reason="model tokenizer not downloaded")
def test_system_prompt_fits_its_budget_in_model_tokens() -> None:
    assert ModelTokenizer(model_tokenizer_path()).count(vf.system_prompt()) <= vf.BUDGETS["system"]


def test_schema_and_decoding() -> None:
    assert vf.VERIFIER_SCHEMA["required"] == [
        "verdict",
        "unmet_requirement",
        "ticket_scope",
        "reason",
    ]
    assert vf.MAX_TOKENS == 200 and vf.TEMPERATURE == 0.0


def test_duplicate_reference_text_is_dropped_keeping_the_higher_ranked(
    index: ix.RetrievalIndex,
) -> None:
    # the fixture has two whoami rules with identical content (ids ...000 and ...003)
    p = prompt("standard", index)
    ref = {d["doc_id"]: d for d in p.manifest["reference"]}
    first, second = "00000000-0000-4000-8000-000000000000", "00000000-0000-4000-8000-000000000003"
    assert ref[first]["dropped"] is False and ref[first]["duplicate_of"] is None
    assert ref[second]["dropped"] is True and ref[second]["duplicate_of"] == first
    assert p.blocks["REFERENCE"].count("[Sigma rule] Test Whoami Discovery") == 1


def test_requirement_and_action_share_one_budget() -> None:
    long_req = "requirement " * 50  # 600 chars ≈ 200 test tokens; with the action block, over
    with pytest.raises(vf.PromptBudgetError, match="action"):
        vf.build_prompt(variant="none", tool="isolate_host", args={"host": H, "cited": [7]},
                        claim=CLAIM, requirement=long_req, evidence=render_cited([ACCESS], COUNTER),
                        reference=[], tickets=[], counter=COUNTER)  # fmt: skip


def test_ticket_block_lists_target_tickets_or_says_none() -> None:
    tickets = vf.target_tickets(context(), {"host": H})
    assert [t.id for t in tickets] == ["CHG-1001"]
    assert vf._ticket_block([]) == "CHANGE_TICKETS: none"
    block = vf._ticket_block(tickets)
    assert block.startswith("CHANGE_TICKETS (times in UTC, as in the records)\n")
    assert '"start": "2020-10-18T09:00:00"' in block and '"approved": true' in block
