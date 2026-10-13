"""T6.1: the live investigator (plan §D.10.4) with a scripted Fake LLM on the mini window."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gbya.agent import investigator as iv
from gbya.data.connection import open_case_db
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.tokens import ApproxCounter
from gbya.tools.registry import ToolEnvironment
from gbya.tools.state import EpisodeState
from tests.unit.test_gate import ctx, mini_db

__all__ = ["mini_db"]

COUNTER = ApproxCounter()
HYPOTHESES = {"thought": "lsass access and powershell look unusual", "hypotheses": [
    {"technique_id": "T1003.001", "behaviour": "a process opens lsass.exe memory with full access"},
    {"technique_id": "T1059.001", "behaviour": "powershell runs an encoded command"},
    {"technique_id": "T1003.001", "behaviour": "duplicate, dropped"},
]}  # fmt: skip
LSASS_SQL = "SELECT * FROM process_access WHERE target_image LIKE '%lsass%' LIMIT 20"
PS_SQL = "SELECT * FROM process_create WHERE image LIKE '%dumper%' LIMIT 20"


def fake(t1059_cites: list[int]) -> FakeLLMClient:
    return FakeLLMClient([  # first match wins: verifier prompts also name the hypothesis
        FakeRule([HYPOTHESES], pattern="WINDOW PROFILE"),
        FakeRule([{"verdict": "ACCEPT", "cited": [5, 7], "reason": "dumper opens lsass"}],
                 pattern=r"HYPOTHESIS\nT1003\.001[\s\S]*QUERY RESULTS"),
        FakeRule([{"verdict": "ACCEPT", "cited": t1059_cites, "reason": "x"}],
                 pattern=r"HYPOTHESIS\nT1059\.001[\s\S]*QUERY RESULTS"),
        FakeRule([{"queries": [{"sql": LSASS_SQL, "purpose": "lsass access"}]}],
                 pattern=r"HYPOTHESIS\nT1003\.001"),
        FakeRule([{"queries": [{"sql": PS_SQL, "purpose": "dumper process"}]}],
                 pattern=r"HYPOTHESIS\nT1059\.001"),
    ])  # fmt: skip


@pytest.fixture
def con(mini_db: Path) -> Iterator[Any]:
    c = open_case_db(mini_db)
    yield c
    c.close()


class Recorder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def __call__(self, query: str, mode: str) -> tuple[Any, list[Any]]:
        from gbya.retrieval.index import Retrieval

        self.queries.append(query)
        return Retrieval(mode="bm25", query_hash="h", sigma_ranking=[], attack_ranking=[]), []


def run(con: Any, client: FakeLLMClient, retrieve: Any = None) -> tuple[iv.Investigation, Any]:
    state = EpisodeState()
    tools = ToolEnvironment(con=con, context=ctx(), state=state, counter=COUNTER)
    inv = iv.Investigator(client=client, tools=tools, counter=COUNTER, retrieve=retrieve,
                          temperature=0.2, seed=11, request_text="Contain host WKSTN-01.").run(con)  # fmt: skip
    return inv, state


def test_profile_is_deterministic(con: Any) -> None:
    p = iv.profile(con)
    assert p["counts"]["process_access"] == 4 and p["counts"]["raw_events"] == 25
    assert iv.profile(con) == p and "WKSTN-01.lab.local" in p["hosts"]
    assert any(x["child"].lower().startswith("dumper") for x in p["rare_parent_child"])


def test_findings_cite_existing_records_returned_by_their_queries(con: Any) -> None:
    rec = Recorder()
    inv, state = run(con, fake(t1059_cites=[999]), rec)
    assert inv.predicted_techniques == ["T1003.001"]
    assert inv.findings == [{"technique_id": "T1003.001",
                             "behaviour": HYPOTHESES["hypotheses"][0]["behaviour"],
                             "cited": [5, 7]}]  # fmt: skip
    ids = {int(r[0]) for r in con.execute("SELECT record_id FROM raw_events").fetchall()}
    assert set(inv.findings[0]["cited"]) <= ids and {5, 7} <= state.retrieved
    t1059 = next(h for h in inv.hypotheses if h.technique_id == "T1059.001")
    assert t1059.verdict == "REJECT" and "999" in t1059.reason  # cited a record it never returned
    assert len(inv.hypotheses) == 2  # the duplicate technique is dropped
    assert inv.llm_calls == 5 and not inv.budget_exhausted  # 1 + 2 x (sqlgen + verifier)
    assert all("T1003" not in q and "T1059" not in q for q in rec.queries)  # never an ID key
    assert any("lsass" in q for q in rec.queries)  # behaviour text and profile terms
    phases = {p["phase"] for _k, p in inv.events}
    assert phases == {"investigation"}


def test_accept_without_citations_is_rejected(con: Any) -> None:
    inv, _ = run(con, fake(t1059_cites=[]))
    t1059 = next(h for h in inv.hypotheses if h.technique_id == "T1059.001")
    assert t1059.verdict == "REJECT" and "without citing" in t1059.reason


def test_budget_is_enforced(con: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(iv, "MAX_LLM_CALLS", 3)
    inv, _ = run(con, fake(t1059_cites=[999]))
    assert inv.budget_exhausted and inv.llm_calls == 3
    assert inv.predicted_techniques == ["T1003.001"]  # the first hypothesis completed


def test_handover_has_findings_and_stubs_what_does_not_fit(con: Any) -> None:
    inv, _ = run(con, fake(t1059_cites=[999]))
    full = iv.handover(inv, COUNTER)
    assert (
        full["findings"].startswith("Accepted techniques: T1003.001") and "5, 7" in full["findings"]
    )
    assert full["queries"][0]["sql"] == LSASS_SQL  # accepted hypotheses first
    assert {5, 7} <= set(full["retrieved"]) and not inv.handover_stubbed
    small = iv.handover(inv, COUNTER, limit=200)
    assert any(q["result"].startswith("[result omitted") for q in small["queries"])
    assert inv.handover_stubbed


def test_unusable_output_is_recorded_and_skipped(con: Any) -> None:
    client = FakeLLMClient([FakeRule(['{"thought": "cut'], pattern="WINDOW PROFILE")])
    inv, _ = run(con, client)
    assert inv.hypotheses == [] and inv.llm_calls == 1
    assert inv.events[-1][1]["error"]
