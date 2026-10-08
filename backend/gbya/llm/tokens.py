"""Token counting with the model's own tokenizer (plan §D.10.3: "Token counts use the model's own
tokenizer"). Used for the 1,500-token tool-result cap, the verifier evidence budget and the
per-request limit.

``ApproxCounter`` exists only for unit tests and CI, where the model files are absent. It is never
chosen silently: ``default_counter`` returns it only when ``GBYA_ENV=test``.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import yaml

from gbya.config import REPO_ROOT, get_settings


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...


class ModelTokenizer:
    def __init__(self, tokenizer_json: Path) -> None:
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_file(str(tokenizer_json))
        self.name = f"model:{tokenizer_json.parent.name}"

    def count(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False).ids)


class ApproxCounter:
    """Test-only estimate: one token per 3 characters (deliberately pessimistic)."""

    name = "approx-test-only"

    def count(self, text: str) -> int:
        return math.ceil(len(text) / 3)


class TokenizerUnavailable(RuntimeError):
    pass


def model_tokenizer_path(profile: str | None = None) -> Path:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "model_profiles.yaml").read_text())
    prof = cfg["profiles"][profile or cfg["default"]]
    return REPO_ROOT / str(prof["model_path"]) / "tokenizer.json"


@lru_cache
def default_counter() -> TokenCounter:
    path = model_tokenizer_path()
    if path.is_file():
        return ModelTokenizer(path)
    if get_settings().env == "test":
        return ApproxCounter()
    raise TokenizerUnavailable(
        f"model tokenizer not found at {path}; fetch the model (T0.3) or set GBYA_ENV=test"
    )
