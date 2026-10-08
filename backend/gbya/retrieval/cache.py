"""Retrieval cache for Exp 1 (plan §D.3 "Stored rankings", T3.7).

``retrieval_rankings`` keeps the **full** candidate rankings (Sigma top 20, ATT&CK top 10, with
BM25 and reranker scores) per (case, mode, query hash, index content hash). Exp 1's verifier reads
them, so every variant of a case sees the same reference and every retrieval metric can be
recomputed from stored rows. The cache is read-through: a missing ranking is computed once with
the same ``RetrievalIndex.retrieve`` and stored; ``make retrieval-cache`` fills it for every case
ahead of a run (the CPU reranker's cost is then paid before the GPU runs). A ``bm25_rerank`` row
is reused only for the same reranker revision.

    python -m gbya.retrieval.cache [case_id ...]      # make retrieval-cache
"""

from __future__ import annotations

import resource
import sys
import time
from collections.abc import Callable
from threading import Lock
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from gbya.retrieval.corpus import Doc
from gbya.retrieval.index import (
    Hit,
    Mode,
    Reranker,
    RerankerUnavailable,
    Retrieval,
    RetrievalIndex,
    query_hash,
)
from gbya.store.db import session_scope
from gbya.store.models import RetrievalRanking

RetrieveMode = Literal["bm25", "bm25_rerank"]


def _hits(rows: list[dict[str, Any]]) -> list[Hit]:
    return [
        Hit(str(r["doc_id"]), int(r["rank"]), float(r["bm25"]),
            None if r.get("rerank") is None else float(r["rerank"]))
        for r in rows
    ]  # fmt: skip


def from_row(row: RetrievalRanking) -> Retrieval:
    mode: Mode = row.mode  # type: ignore[assignment]
    sigma, attack = _hits(row.sigma_ranking), _hits(row.attack_ranking)
    warnings = [f"no {name} document matches the query"
                for name, r in (("Sigma", sigma), ("ATT&CK", attack)) if not r]  # fmt: skip
    return Retrieval(mode, row.query_hash, sigma, attack, warnings)


def reranker_name(reranker: Reranker | None) -> str | None:
    return None if reranker is None else str(getattr(reranker, "name", "custom"))


class RetrievalCache:
    def __init__(
        self,
        factory: sessionmaker[Session],
        index: RetrievalIndex,
        reranker: Callable[[], Reranker] | None = None,
        reranker_id: str | None = None,
    ) -> None:
        self.factory = factory
        self.index = index
        self.index_sha256 = str(index.manifest["content_sha256"])
        self._reranker = reranker
        # Known id of the reranker (repo@revision): a cache hit then never loads the model.
        self.reranker_id = reranker_id
        self._lock = Lock()

    def _get(
        self, s: Session, case_id: str, qh: str, mode: str, rr: str | None
    ) -> Retrieval | None:
        q = select(RetrievalRanking).where(
            RetrievalRanking.case_id == case_id, RetrievalRanking.mode == mode,
            RetrievalRanking.query_hash == qh, RetrievalRanking.index_sha256 == self.index_sha256,
        )  # fmt: skip
        if mode == "bm25_rerank":
            q = q.where(RetrievalRanking.reranker == rr)
        row = s.scalars(q.order_by(RetrievalRanking.id)).first()
        return from_row(row) if row is not None else None

    def retrieve(self, case_id: str, query: str, mode: RetrieveMode) -> Retrieval:
        """The stored ranking for (case, mode, query, index), computed and stored if absent."""
        with self._lock:  # one computation per key, even with parallel Exp 1V units
            reranker: Reranker | None = None
            rr = self.reranker_id if mode == "bm25_rerank" else None
            if mode == "bm25_rerank" and rr is None and self._reranker is not None:
                reranker = self._reranker()
                rr = reranker_name(reranker)
            with session_scope(self.factory) as s:
                hit = self._get(s, case_id, query_hash(query), mode, rr)
                if hit is not None:
                    return hit
            if mode == "bm25_rerank" and reranker is None and self._reranker is not None:
                reranker = self._reranker()  # load only on a miss
                if reranker_name(reranker) != rr:
                    raise RerankerUnavailable(
                        f"loaded reranker {reranker_name(reranker)} is not the expected {rr}"
                    )
            r = self.index.retrieve(query, mode, reranker=reranker)
            with session_scope(self.factory) as s:
                s.add(RetrievalRanking(
                    case_id=case_id, mode=mode, query_hash=r.query_hash,
                    sigma_ranking=[h.to_json() for h in r.sigma_ranking],
                    attack_ranking=[h.to_json() for h in r.attack_ranking],
                    index_sha256=self.index_sha256, reranker=rr if mode == "bm25_rerank" else None,
                ))  # fmt: skip
            return r

    def retriever(self, case_id: str) -> Callable[[str, RetrieveMode], tuple[Retrieval, list[Doc]]]:
        from gbya.gate.verifier import shown_docs

        def retrieve(query: str, mode: RetrieveMode) -> tuple[Retrieval, list[Doc]]:
            r = self.retrieve(case_id, query, mode)
            return r, shown_docs(self.index, r)

        return retrieve


def fill(case_ids: list[str] | None = None) -> None:
    """``make retrieval-cache``: both modes for every case (or the given ones) of app.db."""
    from gbya.config import get_settings
    from gbya.experiments.exp1 import load_case
    from gbya.experiments.runner import Deps, case_query
    from gbya.llm.factory import make_client
    from gbya.llm.tokens import default_counter
    from gbya.store.db import make_engine, make_sessionmaker
    from gbya.store.models import Case

    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    deps = Deps(settings=settings, client=make_client(settings), counter=default_counter())
    cache = deps.cache(factory)
    with session_scope(factory) as s:
        ids = case_ids or list(s.scalars(select(Case.id).where(Case.case_db_path.is_not(None))))
        cases = [load_case(s, cid, settings) for cid in ids]
    modes: tuple[RetrieveMode, ...] = ("bm25", "bm25_rerank")
    timings: dict[str, list[float]] = {m: [] for m in modes}
    for case in cases:
        query = case_query(case, deps.requirements)
        for mode in modes:
            t0 = time.perf_counter()
            cache.retrieve(case.case_id, query, mode)
            timings[mode].append(time.perf_counter() - t0)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    for name, ts in timings.items():
        if ts:
            print(f"{name}: {len(ts)} cases, mean {sum(ts) / len(ts):.3f} s, max {max(ts):.3f} s")
    print(f"peak RSS {peak_mb:.0f} MB; reranker {deps.reranker_name() or 'not loaded'}")


if __name__ == "__main__":
    fill(sys.argv[1:] or None)
