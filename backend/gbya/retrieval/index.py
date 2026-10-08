"""Build, store and query the two content-only BM25 indexes (plan §D.3, T3.1).

``data/index/`` (never committed) holds ``sigma_docs.json``, ``attack_docs.json``,
``gold_map.json`` (rule → technique tags; read only by scoring, never by ``retrieve``) and
``MANIFEST.json`` (source pins, licences, counts, BM25 parameters and a content hash).

``retrieve(query, mode)`` returns the full candidate rankings (Sigma ≤ 20, ATT&CK ≤ 10) and the
parts the verifier sees (Sigma top-5, ATT&CK top-1). Ranking is by BM25 score, ties broken by
document id, so equal inputs give equal outputs. Only documents that share at least one token with
the query are ranked; an empty result is a warning, never an error. ``bm25_rerank`` needs the
CPU reranker (T3.7): without it ``retrieve`` raises ``RerankerUnavailable`` and never falls back
to ``bm25`` (§L.4 item 10).

    python -m gbya.retrieval.index          # make index (fetches the sources first)
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from rank_bm25 import BM25Okapi

from gbya.config import get_settings
from gbya.logging import configure_logging, get_logger
from gbya.retrieval import sources
from gbya.retrieval.corpus import Doc, load_attack, load_sigma
from gbya.retrieval.text import tokenise

Mode = Literal["none", "bm25", "bm25_rerank"]
INDEX_VERSION = 1
BM25_PARAMS = {"k1": 1.5, "b": 0.75, "epsilon": 0.25}
SIGMA_DEPTH, ATTACK_DEPTH = 20, 10  # stored candidate rankings
SIGMA_SHOWN, ATTACK_SHOWN = 5, 1  # given to the verifier

log = get_logger("gbya.retrieval.index")


class RetrievalError(RuntimeError):
    pass


class RerankerUnavailable(RetrievalError):
    """``bm25_rerank`` requested without a working reranker: the item is an error (§D.3)."""


@dataclass(frozen=True)
class Hit:
    doc_id: str
    rank: int  # 1-based, in the final order
    bm25: float
    rerank: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {"doc_id": self.doc_id, "rank": self.rank, "bm25": self.bm25,
                "rerank": self.rerank}  # fmt: skip


@dataclass(frozen=True)
class Retrieval:
    mode: Mode
    query_hash: str
    sigma_ranking: list[Hit]
    attack_ranking: list[Hit]
    warnings: list[str] = field(default_factory=list)

    @property
    def sigma_top5(self) -> list[Hit]:
        return self.sigma_ranking[:SIGMA_SHOWN]

    @property
    def attack_top1(self) -> Hit | None:
        return self.attack_ranking[0] if self.attack_ranking else None


# A reranker maps (query, candidate docs) to one score per doc (T3.7).
Reranker = Callable[[str, Sequence[Doc]], list[float]]


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


class BM25Index:
    def __init__(self, docs: list[Doc]) -> None:
        self.docs = docs
        self.by_id = {d.doc_id: d for d in docs}
        self._tokens = [tokenise(d.index_text) for d in docs]
        self._bm25 = BM25Okapi(self._tokens, **BM25_PARAMS)

    def search(self, tokens: list[str], k: int) -> list[tuple[Doc, float]]:
        if not tokens:
            return []
        q = set(tokens)
        scores = self._bm25.get_scores(tokens)
        cand = [(float(scores[i]), d.doc_id, i) for i, d in enumerate(self.docs)
                if q.intersection(self._tokens[i])]  # fmt: skip
        cand.sort(key=lambda t: (-t[0], t[1]))
        return [(self.docs[i], s) for s, _, i in cand[:k]]


def _rerank(query: str, hits: list[tuple[Doc, float]], reranker: Reranker) -> list[Hit]:
    scores = reranker(query, [d for d, _ in hits])
    if len(scores) != len(hits):
        raise RerankerUnavailable("reranker returned the wrong number of scores")
    order = sorted(zip(hits, scores, strict=True), key=lambda t: (-t[1], t[0][0].doc_id))
    return [Hit(d.doc_id, r, bm, float(s)) for r, ((d, bm), s) in enumerate(order, 1)]


class RetrievalIndex:
    def __init__(self, sigma: list[Doc], attack: list[Doc], manifest: dict[str, Any]) -> None:
        self.sigma = BM25Index(sigma)
        self.attack = BM25Index(attack)
        self.manifest = manifest

    def doc(self, doc_id: str) -> Doc:
        return self.sigma.by_id.get(doc_id) or self.attack.by_id[doc_id]

    def retrieve(self, query: str, mode: Mode, *, reranker: Reranker | None = None) -> Retrieval:
        qh = query_hash(query)
        if mode == "none":
            return Retrieval(mode, qh, [], [])
        if mode not in ("bm25", "bm25_rerank"):
            raise RetrievalError(f"unknown retrieval mode {mode!r}")
        tokens = tokenise(query)
        sigma = self.sigma.search(tokens, SIGMA_DEPTH)
        attack = self.attack.search(tokens, ATTACK_DEPTH)
        if mode == "bm25":
            s_rank = [Hit(d.doc_id, r, s) for r, (d, s) in enumerate(sigma, 1)]
            a_rank = [Hit(d.doc_id, r, s) for r, (d, s) in enumerate(attack, 1)]
        else:
            if reranker is None:
                raise RerankerUnavailable("bm25_rerank needs the CPU reranker (T3.7)")
            s_rank, a_rank = _rerank(query, sigma, reranker), _rerank(query, attack, reranker)
        warnings = [f"no {name} document matches the query"
                    for name, r in (("Sigma", s_rank), ("ATT&CK", a_rank)) if not r]  # fmt: skip
        return Retrieval(mode, qh, s_rank, a_rank, warnings)


# ---- build and load -------------------------------------------------------------------------


def _dump(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")


def default_dir() -> Path:
    s = get_settings()
    return s.resolve(s.data_dir) / "index"


def build(src: sources.Sources, out: Path) -> dict[str, Any]:
    """Write the index files into ``out`` and return the manifest. Deterministic."""
    t0 = time.monotonic()
    rules = load_sigma(src.sigma_dir, src.sigma_commit)
    attack = load_attack(src.attack_file)
    files = {
        "sigma_docs.json": _dump([r.doc.to_json() for r in rules]),
        "attack_docs.json": _dump([d.to_json() for d in attack]),
        "gold_map.json": _dump({
            "sigma_rule_techniques": {r.doc.doc_id: r.techniques for r in rules if r.techniques},
            "attack_techniques": {d.doc_id: d.meta["technique_id"] for d in attack},
        }),
    }  # fmt: skip
    content = hashlib.sha256(b"".join(hashlib.sha256(b).digest() for b in files.values()))
    manifest = {
        "index_version": INDEX_VERSION,
        "content_sha256": content.hexdigest(),
        "files": {k: hashlib.sha256(v).hexdigest() for k, v in files.items()},
        "sigma": {"repo": sources.SIGMA_URL, "release": sources.SIGMA_RELEASE,
                  "commit": src.sigma_commit, "path": "rules/windows", "rules": len(rules),
                  "licence": "Detection Rule License (DRL) 1.1"},
        "attack": {"version": sources.ATTACK_VERSION, "file": src.attack_file.name,
                   "sha256": src.attack_sha256, "techniques": len(attack),
                   "licence": "MITRE ATT&CK terms of use (LICENSE.txt at tag "
                              f"{sources.ATTACK_TAG})"},
        "bm25": BM25_PARAMS,
        "tokeniser": "lower-case, split on non-alphanumerics",
        "depth": {"sigma": SIGMA_DEPTH, "attack": ATTACK_DEPTH},
    }  # fmt: skip
    out.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (out / name).write_bytes(data)
    (out / "MANIFEST.json").write_bytes(_dump(manifest))
    log.info("index_built", out=str(out), seconds=round(time.monotonic() - t0, 1),
             sigma=len(rules), attack=len(attack), content=manifest["content_sha256"])  # fmt: skip
    return manifest


def load(path: Path) -> RetrievalIndex:
    manifest_file = path / "MANIFEST.json"
    if not manifest_file.exists():
        raise RetrievalError(f"no retrieval index at {path}; run `make index`")
    manifest = json.loads(manifest_file.read_text())
    docs = {}
    for name in ("sigma_docs.json", "attack_docs.json"):
        raw = (path / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != manifest["files"][name]:
            raise RetrievalError(f"{name} does not match MANIFEST.json; rebuild with `make index`")
        docs[name] = [Doc.from_json(d) for d in json.loads(raw)]
    return RetrievalIndex(docs["sigma_docs.json"], docs["attack_docs.json"], manifest)


@lru_cache(maxsize=4)
def load_cached(path: Path) -> RetrievalIndex:
    return load(path)


def main() -> None:
    s = get_settings()
    configure_logging("data", s.resolve(s.log_dir), dev=s.env == "dev")
    t0 = time.monotonic()
    src = sources.fetch_all()
    manifest = build(src, default_dir())
    print(json.dumps({k: manifest[k] for k in ("content_sha256", "sigma", "attack")}, indent=2))
    print(f"index built in {time.monotonic() - t0:.1f} s")


if __name__ == "__main__":
    main()
