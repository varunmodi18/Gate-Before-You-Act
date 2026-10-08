"""T3.7: retrieval metrics on hand-computed rankings, the retrieval cache, and the CPU reranker."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gbya.retrieval import index as ix
from gbya.retrieval import sources
from gbya.retrieval.cache import RetrievalCache, from_row
from gbya.retrieval.corpus import Doc
from gbya.retrieval.gold import GoldMap
from gbya.retrieval.metrics import (
    RankedCase,
    case_metrics,
    hit_at_k,
    mrr_at_k,
    ndcg_at_k,
    recall_at_k,
    retrieval_report,
)
from gbya.retrieval.rerank import unavailable_reason
from gbya.store import db
from gbya.store.models import RetrievalRanking

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "retrieval"


# ---- metrics on hand-computed rankings ------------------------------------------------------------


def test_twenty_gold_rules_five_retrieved_all_relevant() -> None:
    gold = {f"g{i}" for i in range(20)}
    ranking = ["g0", "g1", "g2", "g3", "g4", "x1", "x2"]
    assert recall_at_k(ranking, gold) == 0.25  # the plan's example
    assert hit_at_k(ranking, gold) == 1.0
    assert ndcg_at_k(ranking, gold) == 1.0  # ideal over min(5, |G|) = 5 items
    assert mrr_at_k(ranking, gold) == 1.0


def test_hand_computed_partial_ranking() -> None:
    gold = {"a", "b"}
    ranking = ["x", "a", "y", "z", "b", "c"]
    assert recall_at_k(ranking, gold) == 1.0
    dcg = 1 / math.log2(3) + 1 / math.log2(6)
    idcg = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(ranking, gold) == pytest.approx(dcg / idcg)
    assert mrr_at_k(ranking, gold) == 0.5
    assert recall_at_k(["x", "y"], gold) == 0.0 and hit_at_k(["x"], gold) == 0.0
    late = [f"x{i}" for i in range(20)] + ["a"]  # gold only at rank 21: outside MRR@20
    assert mrr_at_k(late, gold) == 0.0
    assert mrr_at_k([*late[:15], "a"], gold) == pytest.approx(1 / 16)


def _gold() -> GoldMap:
    return GoldMap(
        rule_techniques={"r1": ["T1003.001"], "r2": ["T1003"], "r3": ["T1033"]},
        attack_techniques={"a1": "T1003", "a2": "T1003.001", "a3": "T1033"},
    )


def test_report_excludes_empty_gold_sets_and_reports_ceiling_and_attack() -> None:
    cases = [
        RankedCase("s1:E1", "E1", "T1003.001", "bm25", ["r3", "r1"], "a2"),  # G={r1}
        RankedCase("s1:E3", "E3", "T1003", "bm25", ["r3", "x"], "a3"),  # G={r1,r2}; attack wrong
        RankedCase("s2:E1", "E1", "T1055", "bm25", ["r1"], "a1"),  # empty G: excluded
        RankedCase("s1:E1", "E1", "T1003.001", "bm25_rerank", ["r1", "r3"], "a1"),  # parent ok
    ]
    rep = retrieval_report(cases, _gold())
    bm = rep["bm25"]["overall"]
    assert (bm["cases"], bm["sigma_n"], bm["excluded_empty_gold"]) == (3, 2, 1)
    assert bm["recall_at_5"] == 0.5 and bm["hit_at_5"] == 0.5  # (1 + 0) / 2
    assert bm["mrr_at_20"] == 0.25 and bm["mean_gold_size"] == 1.5
    assert bm["mean_recall_ceiling"] == 1.0
    assert bm["attack_n"] == 3 and bm["attack_top1"] == pytest.approx(1 / 3)
    rr = rep["bm25_rerank"]["overall"]
    assert rr["mrr_at_20"] == 1.0 and rr["attack_top1"] == 1.0
    assert rep["bm25"]["by_case_variant"]["E1"]["excluded_empty_gold"] == 1
    empty = case_metrics(cases[2], _gold())
    assert empty["gold_size"] == 0 and "recall_at_5" not in empty


# ---- retrieval cache ----------------------------------------------------------------------------------


class Scored:
    """Stand-in reranker: scores by description length (deterministic)."""

    def __init__(self, name: str = "test-reranker@1") -> None:
        self.name = name

    def __call__(self, query: str, docs: list[Doc]) -> list[float]:
        return [float(len(d.description)) for d in docs]


@pytest.fixture
def cache_env(tmp_path: Path) -> tuple[RetrievalCache, ix.RetrievalIndex, Any, Path]:
    ix.build(sources.Sources(FIX / "sigma", "f" * 40, FIX / "attack/enterprise-attack-test.json",
                             "0" * 64, FIX / "attack/LICENSE.txt"), tmp_path / "index")  # fmt: skip
    index = ix.load(tmp_path / "index")
    db.upgrade(tmp_path / "app.db")
    factory = db.make_sessionmaker(db.make_engine(tmp_path / "app.db"))
    with db.session_scope(factory) as s:  # rankings belong to a case row
        from gbya.store.models import Case, Scenario, Window

        s.add(Window(id="W", title="w"))
        s.flush()
        s.add(Scenario(id="sc", window_id="W"))
        s.flush()
        s.add(Case(id="sc:E1", scenario_id="sc", set_="E", variant="E1", request={}, package={}))
    scored = Scored()
    return RetrievalCache(factory, index, lambda: scored), index, factory, tmp_path / "index"


def test_cache_stores_full_rankings_and_serves_them_again(cache_env: tuple) -> None:  # type: ignore[type-arg]
    cache, index, factory, _ = cache_env
    q = "process_access 10 lsass.exe 0x1010 procdump whoami"
    live = index.retrieve(q, "bm25")
    first = cache.retrieve("sc:E1", q, "bm25")
    assert first == live
    second = cache.retrieve("sc:E1", q, "bm25")
    assert second == live  # served from the stored row
    rr1 = cache.retrieve("sc:E1", q, "bm25_rerank")
    rr2 = cache.retrieve("sc:E1", q, "bm25_rerank")
    assert rr1 == rr2 and all(h.rerank is not None for h in rr1.sigma_ranking)
    with db.session_scope(factory) as s:  # type: ignore[arg-type]
        rows = s.query(RetrievalRanking).order_by(RetrievalRanking.id).all()
        assert [(r.mode, r.reranker) for r in rows] == [
            ("bm25", None),
            ("bm25_rerank", "test-reranker@1"),
        ]
        assert rows[0].index_sha256 == index.manifest["content_sha256"]
        assert len(rows[0].sigma_ranking) == len(live.sigma_ranking)  # full ranking, not top 5
        assert from_row(rows[0]) == live  # every metric can be recomputed from the stored row


def test_metrics_recomputed_from_stored_rankings_equal_live(cache_env: tuple) -> None:  # type: ignore[type-arg]
    cache, index, factory, index_dir = cache_env
    q = "process_access lsass.exe 0x1010"
    live = index.retrieve(q, "bm25")
    cache.retrieve("sc:E1", q, "bm25")
    with db.session_scope(factory) as s:  # type: ignore[arg-type]
        stored = from_row(s.query(RetrievalRanking).one())
    gold = GoldMap.load(index_dir)

    def ranked(r: ix.Retrieval) -> RankedCase:
        return RankedCase("sc:E1", "E1", "T1003.001", r.mode, [h.doc_id for h in r.sigma_ranking],
                          r.attack_top1.doc_id if r.attack_top1 else None)  # fmt: skip

    assert retrieval_report([ranked(live)], gold) == retrieval_report([ranked(stored)], gold)


def test_stale_index_or_other_reranker_is_not_served(cache_env: tuple) -> None:  # type: ignore[type-arg]
    cache, index, factory, _ = cache_env
    q = "lsass"
    cache.retrieve("sc:E1", q, "bm25_rerank")
    other_reranker = Scored("other@2")
    other = RetrievalCache(factory, index, lambda: other_reranker)
    other.retrieve("sc:E1", q, "bm25_rerank")
    cache.index_sha256 = "different-index"
    cache.retrieve("sc:E1", q, "bm25")
    with db.session_scope(factory) as s:  # type: ignore[arg-type]
        rows = [(r.mode, r.reranker, r.index_sha256 == "different-index")
                for r in s.query(RetrievalRanking).order_by(RetrievalRanking.id)]  # fmt: skip
    assert rows == [("bm25_rerank", "test-reranker@1", False), ("bm25_rerank", "other@2", False),
                    ("bm25", None, True)]  # fmt: skip


# ---- the CPU reranker ------------------------------------------------------------------------------------


def test_unavailable_reason_without_the_model(tmp_path: Path) -> None:
    reason = unavailable_reason(tmp_path / "missing")
    assert reason is not None and ("model" in reason or "libraries" in reason)


@pytest.mark.skipif(unavailable_reason() is not None, reason=str(unavailable_reason()))
def test_reranker_process_sees_no_gpu() -> None:
    """Load the reranker in a fresh interpreter (as the worker does) and inspect torch there."""
    code = (
        "import json, torch\n"
        "from gbya.retrieval.rerank import load_reranker\n"
        "from gbya.retrieval.corpus import Doc\n"
        "r = load_reranker()\n"
        "docs = [Doc('a', 'sigma', 'LSASS access', 'process opens lsass', 'x'),"
        " Doc('b', 'sigma', 'Whoami', 'discovery', 'y')]\n"
        "s = r('dumper.exe lsass.exe 0x1010', docs)\n"
        "print(json.dumps({'cuda': torch.cuda.is_available(), 'count': torch.cuda.device_count(),"
        " 'device': r.device, 'scores': s}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CUDA_VISIBLE_DEVICES"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         timeout=300, check=True)  # fmt: skip
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res["cuda"] is False and res["count"] == 0 and res["device"] == "cpu"
    assert res["scores"][0] > res["scores"][1]  # the LSASS document ranks above the unrelated one


def test_cache_hit_never_loads_the_reranker(cache_env: tuple) -> None:  # type: ignore[type-arg]
    _, index, factory, _ = cache_env
    loads = []

    def getter() -> Scored:
        loads.append(1)
        return Scored()

    known = RetrievalCache(factory, index, getter, reranker_id="test-reranker@1")
    known.retrieve("sc:E1", "lsass", "bm25_rerank")  # miss: loads once
    known.retrieve("sc:E1", "lsass", "bm25_rerank")  # hit: no load
    assert loads == [1]
    wrong = RetrievalCache(factory, index, getter, reranker_id="expected@9")
    with pytest.raises(ix.RerankerUnavailable, match="not the expected"):
        wrong.retrieve("sc:E1", "other query", "bm25_rerank")
