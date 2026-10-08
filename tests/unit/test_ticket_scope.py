"""Ticket scope computed in code (diagnostic), the gate ignoring ticket_scope, and research runs
refusing the test-only token counter (team decisions after M3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from gbya.context.models import ChangeTicket
from gbya.experiments.runner import APPROX_COUNTER, refuse_research_tooling
from gbya.experiments.runs import ResearchRunRefused
from gbya.gate.evidence import CitedRecord
from gbya.gate.ticket_scope import agreement, code_ticket_scope, record_matches
from gbya.gate.types import GateVerdict, TicketScope, VerifierOutput
from tests.unit.test_gate import H, con, gate, make_env, mini_db  # noqa: F401


def ticket(tid: str = "CHG-1", *, approved: bool = True, **kw: Any) -> ChangeTicket:
    base = {"id": tid, "host": H, "account": "a.mehta", "command_pattern": r"^backup\.exe ",
            "start": "2020-10-18T09:00:00Z", "end": "2020-10-18T11:00:00Z", "approved": approved}  # fmt: skip
    return ChangeTicket.model_validate({**base, **kw})


def create(rid: int, cmd: str = "backup.exe /all", user: str = "a.mehta", host: str = H,
           ts: datetime = datetime(2020, 10, 18, 10, 0, 0)) -> CitedRecord:  # fmt: skip
    return CitedRecord(rid, "process_create", {"record_id": rid, "event_id": 1, "host": host,
                                               "ts": ts, "command_line": cmd, "user": user})  # fmt: skip


def test_all_four_fields_match_inside_scope() -> None:
    assert record_matches(create(1), ticket()) == dict.fromkeys(
        ("host", "account", "command", "time"), True
    )
    scope = code_ticket_scope([create(1)], [ticket()])
    assert scope["applies"] and scope["ticket_id"] == "CHG-1" and scope["record_id"] == 1


@pytest.mark.parametrize(
    ("rec", "field"),
    [
        (create(1, host="DC-01.lab.local"), "host"),
        (create(1, user="CORP\\\\b.khan"), "account"),
        (create(1, cmd="dumper.exe -p 600"), "command"),
        (create(1, ts=datetime(2020, 10, 18, 12, 0, 0)), "time"),
    ],
)
def test_each_field_can_fail(rec: CitedRecord, field: str) -> None:
    m = record_matches(rec, ticket())
    assert not m[field] and all(v for k, v in m.items() if k != field)
    assert code_ticket_scope([rec], [ticket()])["applies"] is False


def test_domain_prefix_and_case_are_normalised_and_records_without_command_never_match() -> None:
    assert record_matches(create(1, user="CORP\\\\A.Mehta", host=H.upper()), ticket())["account"]
    access = CitedRecord(2, "process_access", {"record_id": 2, "event_id": 10, "host": H,
                                               "ts": datetime(2020, 10, 18, 10), "user": "a.mehta"})  # fmt: skip
    assert record_matches(access, ticket())["command"] is False


def test_only_approved_tickets_count_and_none_means_all_false() -> None:
    none = code_ticket_scope([create(1)], [ticket(approved=False)])
    assert none == {"applies": False, "matches": dict.fromkeys(("host", "account", "command", "time"), False),
                    "ticket_id": None, "record_id": None}  # fmt: skip
    assert code_ticket_scope([create(1)], [])["applies"] is False


def test_best_pair_is_reported() -> None:
    recs = [create(1, cmd="dumper.exe"), create(2)]  # record 2 is fully in scope
    tks = [ticket("CHG-A", host="DC-01.lab.local"), ticket("CHG-B")]
    scope = code_ticket_scope(recs, tks)
    assert (scope["applies"], scope["ticket_id"], scope["record_id"]) == (True, "CHG-B", 2)


def test_agreement_field_by_field() -> None:
    code = code_ticket_scope([create(1, cmd="dumper.exe")], [ticket()])  # host, account, time
    model = {
        "applies": False,
        "matches": {"host": True, "account": False, "command": False, "time": True},
    }
    assert agreement(model, code) == {"applies": True, "host": True, "account": False,
                                      "command": True, "time": True}  # fmt: skip
    assert agreement(None, code) is None


# ---- the gate decides on the verdict alone --------------------------------------------------------


class Fixed:
    def __init__(self, out: VerifierOutput) -> None:
        self.out = out

    def __call__(self, *_: Any, **__: Any) -> VerifierOutput:
        return self.out


@pytest.mark.parametrize("verdict", ["SUPPORTS", "INSUFFICIENT", "CONTRADICTED"])
def test_gate_decision_ignores_ticket_scope(con: Any, verdict: str) -> None:
    decisions = []
    for applies in (False, True):
        scope = TicketScope(
            applies=applies, matches=dict.fromkeys(("host", "account", "command", "time"), applies)
        )
        out = VerifierOutput(verdict=verdict, ticket_scope=scope, reason="r")  # type: ignore[arg-type]
        d = gate("G3", Fixed(out)).evaluate(
            "isolate_host", {"host": H, "cited": [5]}, make_env(con)
        )
        decisions.append((d.verdict, [c.code for c in d.checks]))
    assert decisions[0] == decisions[1]
    assert decisions[0][0] is {"SUPPORTS": GateVerdict.ADMITTED, "INSUFFICIENT": GateVerdict.INSUFFICIENT,
                               "CONTRADICTED": GateVerdict.REJECTED}[verdict]  # fmt: skip


# ---- research runs need the live model and its tokenizer ------------------------------------------


@pytest.mark.parametrize(
    ("backend", "counter", "message"),
    [
        ("vllm", APPROX_COUNTER, "model tokenizer"),
        ("vllm", "unavailable", "model tokenizer"),
        ("fake", "model:Qwen2.5-7B-Instruct-AWQ", "fake"),
        ("replay", "model:Qwen2.5-7B-Instruct-AWQ", "replay"),
    ],
)
def test_research_refuses_test_tooling(backend: str, counter: str, message: str) -> None:
    with pytest.raises(ResearchRunRefused, match=message):
        refuse_research_tooling(backend, counter)
    refuse_research_tooling("vllm", "model:Qwen2.5-7B-Instruct-AWQ")  # the research setup passes
