"""CPU cross-encoder reranker for retrieval mode ``bm25_rerank`` (A6; plan §D.3, T3.7).

``BAAI/bge-reranker-base`` at a pinned revision, loaded from ``models/bge-reranker-base`` (fetched
by ``scripts/fetch_model.py``; its ``MANIFEST.json`` records the commit and every file's SHA-256).
The model runs with ``device="cpu"`` and ``CUDA_VISIBLE_DEVICES=""`` set before torch is imported,
so it can never take GPU memory from the model server. Batch size 16; at most 512 tokens per
(query, document) pair — this bounds the reranker's own input; it is not the verifier's evidence.

If the model or the libraries are missing, ``load_reranker`` raises ``RerankerUnavailable`` and
A6 items are errors; ``bm25_rerank`` never falls back to ``bm25`` (§L.4 item 10).
"""

from __future__ import annotations

import importlib.util
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from gbya.config import REPO_ROOT
from gbya.retrieval.corpus import Doc
from gbya.retrieval.index import RerankerUnavailable

RERANKER_REPO = "BAAI/bge-reranker-base"
RERANKER_REVISION = "2cfc18c9415c912f9d8155881c133215df768a70"  # pinned 8 Oct 2026 (MIT licence)
RERANKER_ID = f"{RERANKER_REPO}@{RERANKER_REVISION}"
MODEL_DIR = REPO_ROOT / "models" / "bge-reranker-base"
BATCH_SIZE = 16
MAX_LENGTH = 512


def unavailable_reason(model_dir: Path = MODEL_DIR) -> str | None:
    """Why the reranker cannot be loaded here, without importing torch (None = it can)."""
    if importlib.util.find_spec("sentence_transformers") is None:
        return "needs the CPU reranker libraries (uv sync --extra rerank)"
    manifest = model_dir / "MANIFEST.json"
    if not manifest.is_file():
        shown = (
            model_dir.relative_to(REPO_ROOT) if model_dir.is_relative_to(REPO_ROOT) else model_dir
        )
        return f"needs the reranker model in {shown}"
    if json.loads(manifest.read_text()).get("revision") != RERANKER_REVISION:
        return f"reranker model is not at the pinned revision {RERANKER_REVISION[:7]}"
    return None


class CrossEncoderReranker:
    """Scores (query, document) pairs; higher is more relevant."""

    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        reason = unavailable_reason(model_dir)
        if reason is not None:
            raise RerankerUnavailable(reason)
        # Hide every GPU before torch is imported in this process (plan §D.3).
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - guarded by unavailable_reason
            raise RerankerUnavailable(f"cannot import sentence_transformers: {exc}") from exc
        self.model: Any = CrossEncoder(str(model_dir), device="cpu", max_length=MAX_LENGTH)
        self.revision = RERANKER_REVISION
        self.name = RERANKER_ID

    @property
    def device(self) -> str:
        return str(self.model.model.device)

    def __call__(self, query: str, docs: Sequence[Doc]) -> list[float]:
        if not docs:
            return []
        pairs = [(query, d.index_text) for d in docs]
        scores = self.model.predict(pairs, batch_size=BATCH_SIZE, show_progress_bar=False)
        return [float(s) for s in scores]


def load_reranker(model_dir: Path = MODEL_DIR) -> CrossEncoderReranker:
    return CrossEncoderReranker(model_dir)
