"""Choose the LLM client from settings, and the model provenance stored with every run (§F.1).

* ``live``: the local model server of the default profile (``config/model_profiles.yaml``).
* ``fake``: a scripted client for tests and the Playwright profile. It answers every verifier
  call with SUPPORTS and says so in its reason. Runs on it are never research runs.
* ``replay``: a recorded cassette. Runs on it are flagged ``replay`` and excluded from analysis.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import yaml

from gbya.config import REPO_ROOT, Settings
from gbya.llm.client import LLMClient
from gbya.llm.fake import FakeLLMClient, FakeRule
from gbya.llm.schemas import JsonSchema, Message, Usage

FAKE_VERIFIER_OUTPUT: dict[str, Any] = {
    "verdict": "SUPPORTS",
    "unmet_requirement": None,
    "ticket_scope": {"applies": False,
                     "matches": {"host": False, "account": False, "command": False, "time": False}},
    "reason": "Fake LLM backend (test profile): fixed SUPPORTS, not a model judgement.",
}  # fmt: skip


def _profile(name: str | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load((REPO_ROOT / "config" / "model_profiles.yaml").read_text())
    prof: dict[str, Any] = cfg["profiles"][name or cfg["default"]]
    return prof


def model_provenance(settings: Settings) -> dict[str, Any]:
    """``model_id``, ``model_file_sha256``, ``backend`` and ``backend_flags`` for a run row.

    ``model_file_sha256`` is the SHA-256 of the canonical JSON of the per-file SHA-256 list in
    the model's ``MANIFEST.json`` (the AWQ weights are split over two files), so one value pins
    every file the server loads."""
    if settings.llm_backend == "fake":
        return {"model_id": "fake", "model_file_sha256": None, "backend": "fake",
                "backend_flags": {"delay_ms": settings.fake_llm_delay_ms}}  # fmt: skip
    prof = _profile()
    manifest = REPO_ROOT / str(prof["model_path"]) / "MANIFEST.json"
    digest = None
    if manifest.is_file():
        files = json.loads(manifest.read_text())["files"]
        canon = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(canon).hexdigest()
    backend = str(prof["backend"]) if settings.llm_backend == "live" else "replay"
    return {
        "model_id": f"{prof['model_repo']}@{prof['model_revision']}",
        "model_file_sha256": digest,
        "backend": backend,
        "backend_flags": {"version": prof.get("backend_version"), "command": prof["command"],
                          "cassette": str(settings.replay_cassette)
                          if settings.llm_backend == "replay" else None},
    }  # fmt: skip


class _DelayedFake(FakeLLMClient):
    """The fake client with a per-call delay (worker kill-and-resume tests)."""

    def __init__(self, delay_ms: int, rules: list[FakeRule]) -> None:
        super().__init__(rules)
        self.delay_s = delay_ms / 1000

    def chat_json(self, messages: list[Message], schema: JsonSchema, *, temperature: float,
                  seed: int | None, max_tokens: int) -> tuple[dict[str, Any], Usage]:  # fmt: skip
        if self.delay_s:
            time.sleep(self.delay_s)
        return super().chat_json(messages, schema, temperature=temperature, seed=seed,
                                 max_tokens=max_tokens)  # fmt: skip


def make_client(settings: Settings) -> LLMClient:
    if settings.llm_backend == "fake":
        return _DelayedFake(settings.fake_llm_delay_ms, [FakeRule([FAKE_VERIFIER_OUTPUT])])
    if settings.llm_backend == "replay":
        from gbya.llm.replay import ReplayLLMClient

        if settings.replay_cassette is None:
            raise ValueError("llm_backend=replay needs replay_cassette")
        return ReplayLLMClient(settings.resolve(settings.replay_cassette))
    from gbya.llm.live import LiveLLMClient

    return LiveLLMClient(settings.model_base_url, str(_profile()["served_model_name"]))
