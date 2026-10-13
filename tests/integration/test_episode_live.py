"""T5.1/T5.2 live smoke: one G3 episode on the mini window with the real proposer and verifier.

Needs `make model-up` and `make index`; run with `uv run pytest -m gpu -s`. Prints the trace;
asserts only that the episode reached a terminal state (no assertion on behaviour).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gbya.agent import episode as ep
from gbya.cases.models import Request
from gbya.config import REPO_ROOT
from gbya.data.catalogue import parse_metadata
from gbya.data.connection import open_case_db
from gbya.data.normalise import normalise_window
from gbya.gate import verifier as vf
from gbya.gate.checks import GateEnv
from gbya.gate.config import load_configs
from gbya.gate.evidence import window_time_range
from gbya.gate.gate import Gate
from gbya.llm.factory import _profile
from gbya.llm.live import LiveLLMClient
from gbya.llm.tokens import ModelTokenizer, model_tokenizer_path
from gbya.policy.engine import PolicyEngine, load_evidence_requirements
from gbya.retrieval import index as ix
from gbya.tools.names import ALL_TOOLS
from gbya.tools.registry import ToolEnvironment
from gbya.tools.state import EpisodeState
from tests.unit.test_gate import H, ctx

MINI = Path(__file__).resolve().parents[1] / "fixtures" / "mini_window"


@pytest.mark.gpu
def test_live_g3_episode(tmp_path: Path) -> None:
    duck = tmp_path / "mini.duckdb"
    normalise_window(
        parse_metadata(MINI / "datasets/atomic/_metadata/SDWIN-MINI-000001.yaml", MINI), MINI, duck
    )
    con = open_case_db(duck)
    client = LiveLLMClient("http://127.0.0.1:8001/v1", str(_profile()["served_model_name"]))
    reqs = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
    verifier = vf.LLMVerifier(client, "standard", reqs,
                              vf.live_retriever(ix.load(REPO_ROOT / "data" / "index")))  # fmt: skip
    gate = Gate(load_configs()["G3"], PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml"),
                verifier)  # fmt: skip
    counter = ModelTokenizer(model_tokenizer_path())
    state, context = EpisodeState(), ctx("unreachable")
    tools = ToolEnvironment(con=con, context=context, state=state, counter=counter)
    # A prefix as the case builder makes one: canned queries through the agent's own sql_query.
    queries = []
    for sql in (
        "SELECT * FROM process_create WHERE record_id IN (1, 2, 3, 4) ORDER BY record_id",
        "SELECT * FROM process_access WHERE record_id IN (5, 6, 7, 8) ORDER BY record_id",
    ):
        res = tools.run_read_only("sql_query", {"sql": sql})
        assert res.ok, res.content
        queries.append({"sql": sql, "result": res.content})
    prefix = ep.prefix_text({"profile": "Profile of window SDWIN-MINI-000001: 25 events.",
                             "queries": queries,
                             "findings": "Records 1, 5 look relevant to the request; deciding on "
                             "the next action."})  # fmt: skip
    trace = ep.MemoryTrace()
    result = ep.EpisodeRunner(
        client=client, gate=gate,
        env=GateEnv(con=con, ctx=context, state=state, counter=counter, allowed_tools=ALL_TOOLS,
                    window=window_time_range(con)),
        tools=tools,
        request=Request(objective="contain_host", target={"host": H},
                        text=f"Credential dumping is suspected on {H}. Contain the host."),
        prefix=prefix, temperature=0.2, seed=11, trace=trace, counter=counter,
    ).run()  # fmt: skip
    con.close()
    for kind, payload in trace.steps:
        shown = payload.get("proposal") or payload.get("text") or payload.get("decision", {}).get(
            "verdict") or payload.get("content", "")  # fmt: skip
        print(f"{kind:9} {json.dumps(shown, ensure_ascii=False)[:220]}")
    print(f"result: {result.terminal_state} ({result.reason}); LLM calls {result.llm_calls}; "
          f"steps {result.steps}; tokens in/out {result.tokens_in}/{result.tokens_out}; "
          f"unusable outputs {result.invalid_outputs}; truncations {result.truncations}")  # fmt: skip
    assert result.terminal_state in ("fulfilled", "handed_off", "ended", "cap")
