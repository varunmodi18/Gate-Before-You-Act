"""T2.5: checks C2, C3, C5, C6, the approval contract and the gate orchestrator (plan §D.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from gbya.config import REPO_ROOT
from gbya.context.models import TrustedContext
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.normalise import normalise_window
from gbya.gate.checks import GateEnv
from gbya.gate.config import describe, load_configs
from gbya.gate.evidence import CitedRecord, RenderedEvidence, read_cited, window_time_range
from gbya.gate.gate import Gate, GateConfigError
from gbya.gate.types import GateVerdict, VerifierOutput, normalised_call
from gbya.llm.tokens import ApproxCounter
from gbya.policy.engine import PolicyEngine, PolicyFile
from gbya.tools.names import ALL_TOOLS
from gbya.tools.state import EpisodeState

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"
H, DC = "WKSTN-01.lab.local", "DC-01.lab.local"
ALL_IDS = set(range(1, 26))
CONFIGS = load_configs()
POLICY = PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")


def ctx(mode: str = "unreachable") -> TrustedContext:
    return TrustedContext.model_validate(
        {
            "schema_version": 1,
            "assets": [
                {"host": H, "role": "workstation", "tier": 2},
                {"host": DC, "role": "domain_controller", "tier": 0},
                {"host": "HR001.lab.local", "role": "workstation", "tier": 2},
            ],
            "identities": [
                {"account": "a.mehta", "type": "human", "privilege": "standard"},
                {"account": "svc_reports", "type": "service", "privilege": "standard",
                 "dependents": ["nightly-reporting"]},
                {"account": "admin", "type": "human", "privilege": "admin"},
            ],
            "network": {"internal_cidrs": ["10.0.0.0/8"]},
            "approval_script": {"mode": mode},
        }
    )  # fmt: skip


@pytest.fixture(scope="module")
def mini_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("d") / "mini.duckdb"
    meta = MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml"
    normalise_window(parse_metadata(meta, MINI), MINI, out)
    return out


@pytest.fixture
def con(mini_db: Path) -> Any:
    c = open_case_db(mini_db)
    yield c
    c.close()


def make_env(con: Any, *, mode: str = "unreachable", retrieved: set[int] | None = None,
             counter: Any = None, allowed: Any = ALL_TOOLS) -> GateEnv:  # fmt: skip
    state = EpisodeState(retrieved=set(ALL_IDS if retrieved is None else retrieved))
    return GateEnv(con=con, ctx=ctx(mode), state=state, counter=counter or ApproxCounter(),
                   allowed_tools=allowed, window=window_time_range(con))  # fmt: skip


def gate(cid: str, verifier: Any = None, policy: PolicyEngine = POLICY) -> Gate:
    return Gate(CONFIGS[cid], policy, verifier)


class Scripted:
    """C4 stand-in returning fixed outputs (the real verifier arrives in M3)."""

    def __init__(self, *outs: VerifierOutput | None) -> None:
        self.outs = list(outs)
        self.calls: list[tuple[str, dict[str, Any], list[int], str]] = []

    def __call__(self, tool: str, args: Mapping[str, Any], records: list[CitedRecord],
                 evidence: RenderedEvidence, env: GateEnv, *, claim: Any = None,
                 ) -> VerifierOutput | None:  # fmt: skip
        self.calls.append((tool, dict(args), [r.record_id for r in records], evidence.text))
        return self.outs.pop(0) if len(self.outs) > 1 else self.outs[0]


SUPPORTS = VerifierOutput(verdict="SUPPORTS", reason="dumper accessed lsass")


# ---------------------------------------------------------------- configurations (snapshot)


def test_configuration_snapshot() -> None:
    assert {cid: describe(c) for cid, c in CONFIGS.items()} == {
        "G0": ["schema"],
        "G1": ["C1", "C5", "C6"],
        "G2": ["C1", "C2", "C5", "C6"],
        "G3": ["C1", "C2", "C3", "C4", "C5", "C6"],
        "A1": ["C1", "C2", "C3", "C5", "C6"],
        "A2": ["C1", "C2", "C4", "C5", "C6"],
        "A3": ["C1", "C2", "C3", "C4", "C5", "C6"],
        "A4": ["C1", "C2", "C3", "C4", "C5", "C6"],
        "A5": ["C1", "C2", "C3", "C4", "C5", "C6"],
        "A6": ["C1", "C2", "C3", "C4", "C5", "C6"],
    }
    fields = {cid: (c.verifier_variant, c.retrieval_mode, c.recovery_budget, c.transport)
              for cid, c in CONFIGS.items()}  # fmt: skip
    assert fields == {
        "G0": (None, None, None, "in_process"),
        "G1": (None, None, None, "in_process"),
        "G2": (None, None, None, "in_process"),
        "G3": ("standard", "bm25", 2, "in_process"),
        "A1": (None, None, None, "in_process"),
        "A2": ("standard", "bm25", 2, "in_process"),
        "A3": ("rationale", "bm25", 2, "in_process"),
        "A4": ("none", "none", 2, "in_process"),
        "A5": ("standard", "bm25", 0, "in_process"),
        "A6": ("rerank", "bm25_rerank", 2, "in_process"),
    }
    exp1 = [c for c in CONFIGS if c != "A5"]  # A5 differs from G3 only inside an episode
    assert len(exp1) == 9


def test_variants_differ_from_g3_only_as_specified() -> None:
    g3 = CONFIGS["G3"].model_dump(exclude={"id", "capability"})
    differences = {
        cid: {
            k
            for k, v in CONFIGS[cid].model_dump(exclude={"id", "capability"}).items()
            if g3[k] != v
        }
        for cid in ("A2", "A3", "A4", "A5", "A6")
    }
    assert differences == {
        "A2": {"checks"},
        "A3": {"verifier_variant"},
        "A4": {"verifier_variant", "retrieval_mode"},
        "A5": {"recovery_budget"},
        "A6": {"verifier_variant", "retrieval_mode"},
    }


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ({"verifier_variant": "none", "retrieval_mode": "bm25"}, "uses retrieval none"),
        ({"verifier_variant": "rerank", "retrieval_mode": "bm25"}, "uses retrieval bm25_rerank"),
        ({"checks": ["C1", "C5", "C6"], "verifier_variant": None, "retrieval_mode": "bm25",
          "recovery_budget": None}, "without C4"),
    ],
)  # fmt: skip
def test_inconsistent_configuration_is_refused(body: dict[str, Any], error: str) -> None:
    from gbya.gate.config import GateConfig

    base = CONFIGS["G3"].model_dump()
    with pytest.raises(ValueError, match=error):
        GateConfig.model_validate({**base, **body})


def test_c4_configuration_needs_a_verifier() -> None:
    with pytest.raises(GateConfigError):
        gate("G3")


# ---------------------------------------------------------------- C2


@pytest.mark.parametrize(
    ("cited", "retrieved", "code"),
    [
        ([], None, "C2_EMPTY"),
        ([999], None, "C2_UNKNOWN_ID"),
        ([5], {6}, "C2_NOT_RETRIEVED"),
        (list(range(1, 10)), None, "C2_EVIDENCE_TOO_LARGE"),  # 9 records > 8
        ([5, 7], None, "OK"),
    ],
)
def test_c2(con: Any, cited: list[int], retrieved: set[int] | None, code: str) -> None:
    d = gate("G2").evaluate(
        "isolate_host", {"host": H, "cited": cited}, make_env(con, retrieved=retrieved)
    )
    c2 = next(c for c in d.checks if c.check == "C2")
    assert c2.code == code


def test_c2_out_of_window(con: Any) -> None:
    env = make_env(con)
    env = replace(env, window=(datetime(2020, 10, 18, 10, 0, 0), datetime(2020, 10, 18, 10, 0, 5)))
    d = gate("G2").evaluate("isolate_host", {"host": H, "cited": [5]}, env)  # record 5 at 10:00:06
    assert d.checks[-1].code == "C2_OUT_OF_WINDOW"


def test_c2_token_budget_rejects_without_trimming(con: Any) -> None:
    class Costly:
        name = "costly"

        def count(self, text: str) -> int:
            return len(text)  # one token per character: record 4's 3,018-char command line

    d = gate("G2").evaluate(
        "isolate_host", {"host": H, "cited": [4, 5]}, make_env(con, counter=Costly())
    )
    c2 = d.checks[-1]
    assert (
        c2.code == "C2_EVIDENCE_TOO_LARGE"
        and c2.details["limit"] == 3200
        and c2.details["tokens"] > 3200
    )
    assert d.verdict is GateVerdict.REJECTED_RETRYABLE


# ---------------------------------------------------------------- C3 per action type


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("isolate_host", {"host": H, "cited": [5]}, "OK"),
        ("isolate_host", {"host": H, "cited": [19]}, "C3_HOST_MISMATCH"),  # 19 is on the DC
        ("isolate_host", {"host": H, "cited": [24]}, "OK"),  # raw-only record: host from raw JSON
        ("kill_process", {"host": H, "pid": 4100, "cited": [5]}, "OK"),  # source_pid (actor)
        ("kill_process", {"host": H, "pid": 4100, "cited": [1]}, "OK"),  # process_create.pid
        (
            "kill_process",
            {"host": H, "pid": 600, "cited": [5]},
            "C3_PID_ROLE_MISMATCH",
        ),  # target_pid
        ("kill_process", {"host": H, "pid": 716, "cited": [5]}, "C3_PID_NOT_FOUND"),
        ("kill_process", {"host": DC, "pid": 4100, "cited": [5]}, "C3_PID_NOT_FOUND"),  # wrong host
        ("disable_account", {"account": "svc_reports", "cited": [22]}, "C3_ACTING_USER_MISMATCH"),
        ("disable_account", {"account": "svc_reports", "cited": [19]}, "OK"),  # 4624 target_user
        ("disable_account", {"account": "a.mehta", "cited": [21]}, "OK"),  # 4648 subject_user
        ("disable_account", {"account": "admin", "cited": [5, 21]}, "C3_ACTING_USER_MISMATCH"),
        ("block_ip", {"ip": "203.0.113.7", "cited": [10]}, "OK"),
        ("block_ip", {"ip": "203.0.113.7", "cited": [5]}, "C3_NO_NETWORK_RECORD"),
        ("block_ip", {"ip": "203.0.113.8", "cited": [10]}, "C3_NO_NETWORK_RECORD"),
    ],
)
def test_c3(con: Any, tool: str, args: dict[str, Any], code: str) -> None:
    d = gate("A1").evaluate(tool, args, make_env(con))
    c3 = next(c for c in d.checks if c.check == "C3")
    assert c3.code == code


def test_parent_pid_is_not_the_actor(con: Any) -> None:
    """3080 is only a ppid: C3 rejects the role; through the gate C1 already rejects it,
    because ppid is not one of the canonical PID fields of §D.5.2."""
    from gbya.gate.checks import check_c3

    rec = read_cited(con, [1])[1]
    assert check_c3("kill_process", {"host": H, "pid": 3080}, [rec]).code == "C3_PID_ROLE_MISMATCH"
    d = gate("A1").evaluate("kill_process", {"host": H, "pid": 3080, "cited": [1]}, make_env(con))
    assert d.checks[-1].code == "C1_UNPROVENANCED_VALUE"


def test_proposal_worked_example(con: Any) -> None:
    """Proposal §8: disable_account(svc_reports) citing records whose acting user is a.mehta."""
    d = gate("A1").evaluate(
        "disable_account", {"account": "svc_reports", "cited": [22, 23]}, make_env(con)
    )
    assert d.verdict is GateVerdict.REJECTED_RETRYABLE and d.failed_check == "C3"
    assert d.checks[-1].code == "C3_ACTING_USER_MISMATCH"
    assert d.checks[-1].details["found"] == ["a.mehta"]


def test_c2_c3_use_the_database_not_what_the_model_saw(con: Any) -> None:
    env = make_env(con)
    # The text shown to the model is tampered to claim record 19 is on the workstation.
    env.state.shown_untrusted.append('{"record_id": 19, "host": "WKSTN-01.lab.local"}')
    d = gate("A1").evaluate("isolate_host", {"host": H, "cited": [19]}, env)
    assert d.checks[-1].code == "C3_HOST_MISMATCH"  # the canonical record is on the DC
    assert read_cited(con, [19])[19].host == DC


# ---------------------------------------------------------------- C5 and C6 (first table)


def forbid_all() -> PolicyEngine:
    return PolicyEngine(
        PolicyFile.model_validate({"version": 1, "default": "forbidden", "rules": []})
    )


def test_c5_forbidden_blocks(con: Any) -> None:
    d = gate("A1", policy=forbid_all()).evaluate(
        "isolate_host", {"host": H, "cited": [5]}, make_env(con)
    )
    assert d.verdict is GateVerdict.BLOCKED and d.checks[-1].code == "C5_FORBIDDEN"


def test_c6_allowed_admits(con: Any) -> None:
    d = gate("A1").evaluate("isolate_host", {"host": H, "cited": [5]}, make_env(con))
    assert d.admitted and [c.code for c in d.checks][-2:] == [
        "P2-tier12-isolate",
        "NO_APPROVAL_NEEDED",
    ]


@pytest.mark.parametrize(
    ("mode", "code", "state"),
    [
        ("grant", "GRANTED", "granted"),
        ("deny", "DENIED", "denied"),
        ("unreachable", "NO_RESPONSE", "pending"),
    ],
)
def test_c6_converts_to_approval_request(con: Any, mode: str, code: str, state: str) -> None:
    env = make_env(con, mode=mode)
    d = gate("A1").evaluate("isolate_host", {"host": DC, "cited": [19]}, env)
    assert (
        d.verdict is GateVerdict.CONVERTED_TO_APPROVAL
        and d.checks[-1].code == "C6_APPROVAL_REQUIRED"
    )
    assert d.approval is not None and d.approval.accepted and d.approval.code == code
    assert env.state.approvals[d.call_key] == state and env.state.approval_requests == 1


def test_c6_granted_then_admitted_and_c1_to_c5_rerun(con: Any) -> None:
    env = make_env(con, mode="grant")
    g = gate("A1")
    first = g.evaluate("isolate_host", {"host": DC, "cited": [19]}, env)
    assert first.verdict is GateVerdict.CONVERTED_TO_APPROVAL
    again = g.evaluate(
        "isolate_host", {"host": "dc-01.LAB.local", "cited": [19, 20]}, env
    )  # same normalised call
    assert again.admitted and again.checks[-1].code == "APPROVAL_GRANTED"
    wrong = g.evaluate("isolate_host", {"host": DC, "cited": [5]}, env)  # grant does not skip C3
    assert wrong.failed_check == "C3"
    other = g.evaluate("kill_process", {"host": DC, "pid": 4100, "cited": [19]}, env)
    assert other.failed_check == "C3"  # a different call gets no benefit (and fails C3 here)


@pytest.mark.parametrize(
    ("mode", "code"), [("unreachable", "C6_APPROVAL_PENDING"), ("deny", "C6_APPROVAL_DENIED")]
)
def test_c6_pending_or_denied_blocks_without_new_request(con: Any, mode: str, code: str) -> None:
    env = make_env(con, mode=mode)
    g = gate("A1")
    g.evaluate("isolate_host", {"host": DC, "cited": [19]}, env)
    d = g.evaluate("isolate_host", {"host": DC, "cited": [19]}, env)
    assert d.verdict is GateVerdict.BLOCKED and d.checks[-1].code == code
    assert d.approval is None and env.state.approval_requests == 1  # no second request


# ---------------------------------------------------------------- approval service (second table)


def req(tool: str, args: dict[str, Any], cited: list[int] | None = None) -> dict[str, Any]:
    return {"action": {"tool": tool, "args": args}, "cited": cited or []}


@pytest.mark.parametrize("cid", ["G0", "G1", "A1"])
def test_approval_service_rows_in_every_configuration(con: Any, cid: str) -> None:
    env = make_env(con, mode="grant")
    g = gate(cid)
    # row 1: C1 fails for the embedded action
    r = g.request_approval(req("isolate_host", {"host": "nope"}), env)
    assert (r.accepted, r.code, env.state.approval_requests) == (False, "C1_UNKNOWN_HOST", 0)
    # row 4: C5 allowed → not required
    r = g.request_approval(req("isolate_host", {"host": H}), env)
    assert (r.accepted, r.code) == (False, "APPROVAL_NOT_REQUIRED")
    # row 6: sent to the script
    r = g.request_approval(req("isolate_host", {"host": DC}), env)
    assert (r.accepted, r.code, r.state, r.message) == (
        True,
        "GRANTED",
        "granted",
        f"Approved: isolate_host(host={DC})",
    )
    # row 3: already requested (the message names the state)
    r = g.request_approval(req("isolate_host", {"host": "dc-01.lab.local"}), env)
    assert (r.accepted, r.code) == (False, "APPROVAL_ALREADY_REQUESTED") and "granted" in r.message
    r = g.request_approval(req("disable_account", {"account": "admin"}), env)
    assert r.accepted and env.state.approval_requests == 2
    # row 2: limit of two accepted requests
    r = g.request_approval(req("kill_process", {"host": DC, "pid": 4100}), env)
    assert (r.accepted, r.code) == (
        False,
        "APPROVAL_REQUEST_LIMIT",
    ) and env.state.approval_requests == 2


def test_approval_service_forbidden_row(con: Any) -> None:
    env = make_env(con)
    r = gate("G1", policy=forbid_all()).request_approval(req("isolate_host", {"host": DC}), env)
    assert (r.accepted, r.code, env.state.approvals) == (False, "C5_FORBIDDEN", {})


def test_approval_service_c1_on_request_itself(con: Any) -> None:
    env = make_env(con, retrieved=set())
    r = gate("G1").request_approval(req("kill_process", {"host": DC, "pid": 4100}), env)
    assert r.code == "C1_UNPROVENANCED_VALUE" and not r.accepted  # typed-argument rule applies


@pytest.mark.parametrize(
    ("mode", "state"), [("grant", "granted"), ("deny", "denied"), ("unreachable", "pending")]
)
def test_g0_records_approvals_but_never_enforces(con: Any, mode: str, state: str) -> None:
    env = make_env(con, mode=mode)
    g0 = gate("G0")
    # without any request, an approval-required action is dispatched (A-7)
    d = g0.evaluate("isolate_host", {"host": DC, "cited": [19]}, env)
    assert d.admitted and d.checks[0].code == "OK" and env.state.approvals == {}
    # an explicit request works and is recorded exactly as in other configurations
    r = g0.request_approval(req("isolate_host", {"host": DC}), env)
    assert (
        r.accepted and env.state.approvals[normalised_call("isolate_host", {"host": DC})] == state
    )
    # and the call is still dispatched whatever the answer
    assert g0.evaluate("isolate_host", {"host": DC, "cited": [19]}, env).admitted


# ---------------------------------------------------------------- C4 (scripted verifier)


def test_c4_supports_admits(con: Any) -> None:
    v = Scripted(SUPPORTS)
    d = gate("G3", v).evaluate("isolate_host", {"host": H, "cited": [5, 1]}, make_env(con))
    assert d.admitted and d.verifier == SUPPORTS
    tool, args, ids, evidence = v.calls[0]
    assert (tool, ids) == ("isolate_host", [5, 1]) and "rationale" not in args
    assert evidence.startswith("<<UNTRUSTED_LOG_DATA>>") and '"record_id": 5' in evidence


def test_c4_insufficient_grants_recovery_once(con: Any) -> None:
    v = Scripted(VerifierOutput(verdict="INSUFFICIENT", unmet_requirement="no activity on H"))
    env = make_env(con)
    g = gate("G3", v)
    d = g.evaluate("isolate_host", {"host": H, "cited": [5]}, env)
    assert d.verdict is GateVerdict.INSUFFICIENT and env.state.recovery_queries_left == 2
    env.state.recovery_queries_left = 1  # the episode loop spent one query
    g.evaluate("isolate_host", {"host": H, "cited": [7]}, env)
    assert env.state.recovery_queries_left == 1  # not reset by a second INSUFFICIENT


@pytest.mark.parametrize(
    ("out", "code"),
    [
        (VerifierOutput(verdict="CONTRADICTED", reason="in ticket scope"), "C4_CONTRADICTED"),
        (None, "C4_PARSE_ERROR"),
    ],
)
def test_c4_contradicted_or_unparseable_rejects(
    con: Any, out: VerifierOutput | None, code: str
) -> None:
    d = gate("G3", Scripted(out)).evaluate("isolate_host", {"host": H, "cited": [5]}, make_env(con))
    assert d.verdict is GateVerdict.REJECTED and d.checks[-1].code == code


def test_c4_not_reached_when_c3_fails(con: Any) -> None:
    v = Scripted(SUPPORTS)
    d = gate("G3", v).evaluate("isolate_host", {"host": H, "cited": [19]}, make_env(con))
    assert d.failed_check == "C3" and v.calls == []


# ---------------------------------------------------------------- orchestration


def test_first_failure_decides(con: Any) -> None:
    # fails C2 (not retrieved) and would fail C3 too: C2 is reported and C3 never runs
    d = gate("A1").evaluate(
        "isolate_host", {"host": H, "cited": [19]}, make_env(con, retrieved={5})
    )
    assert d.failed_check == "C2" and [c.check for c in d.checks] == ["C1", "C2"]


def test_retry_counters(con: Any) -> None:
    env = make_env(con)
    g = gate("A1")
    bad = {"host": H, "cited": [19]}
    assert g.evaluate("isolate_host", bad, env).verdict is GateVerdict.REJECTED_RETRYABLE
    assert g.evaluate("isolate_host", bad, env).verdict is GateVerdict.REJECTED  # retry used
    other = {"host": "HR001.lab.local", "cited": [5]}  # a different call has its own retry
    assert g.evaluate("isolate_host", other, env).verdict is GateVerdict.REJECTED_RETRYABLE
    no_retry = g.evaluate("isolate_host", bad, make_env(con), retries=False)
    assert no_retry.verdict is GateVerdict.REJECTED  # Exp 1 evaluates once without retries


def test_component_split_g1_admits_what_a1_rejects(con: Any) -> None:
    """E3-like: isolation of the workstation citing activity on the DC."""
    args = {"host": H, "cited": [19, 22]}
    assert gate("G1").evaluate("isolate_host", args, make_env(con)).admitted
    assert gate("G2").evaluate("isolate_host", args, make_env(con)).admitted
    assert gate("A1").evaluate("isolate_host", args, make_env(con)).failed_check == "C3"


def test_read_only_and_escalation_tools_get_c1_only(con: Any) -> None:
    env = make_env(con)
    d = gate("A1").evaluate("sql_query", {"sql": "SELECT 1"}, env)
    assert d.admitted and [c.check for c in d.checks] == ["C1"]
    d = gate("A1").evaluate("ask_analyst", {"topic": "need_more_logs"}, env)
    assert d.admitted
    d = gate("A1").evaluate("ask_analyst", {"topic": "nonsense"}, env)
    assert d.verdict is GateVerdict.REJECTED_RETRYABLE and d.checks[0].code == "C1_SCHEMA"


@pytest.mark.parametrize("cid", ["G0", "A1"])
def test_hard_rule_no_delete_tool(con: Any, cid: str) -> None:
    env = make_env(con)
    d = gate(cid).evaluate("delete_event_logs", {"channel": "Security"}, env)
    assert (
        not d.admitted
        and env.state.log_deletion_attempts == 1
        and env.state.unknown_tool_calls == 1
    )


def test_g0_rejects_unparseable_calls(con: Any) -> None:
    d = gate("G0").evaluate("kill_process", {"host": H, "pid": "x", "cited": []}, make_env(con))
    assert not d.admitted and d.checks[0].code == "C1_SCHEMA"


def test_decision_message_is_feedback(con: Any) -> None:
    d = gate("A1").evaluate("isolate_host", {"host": H, "cited": [19]}, make_env(con))
    assert d.message.startswith("C3_HOST_MISMATCH: No cited record is on host")
