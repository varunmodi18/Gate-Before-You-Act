"""Choose the LLM client from settings, and the model provenance stored with every run (§F.1).

* ``live``: the local model server of the default profile (``config/model_profiles.yaml``).
* ``fake``: a scripted client for tests and the Playwright profile (``fake_rules``). The verifier
  answers INSUFFICIENT when no cited record names a dumping tool ("dump", any case: the fixtures'
  ``dumper.exe`` and ``Outflank-Dumpert.exe``) and SUPPORTS otherwise, and says so in its
  reason; the proposer follows a fixed recovery script written for the e2e fixture (the mini
  window). Runs on it are never research runs.
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


FAKE_INSUFFICIENT: dict[str, Any] = {
    **FAKE_VERIFIER_OUTPUT,
    "verdict": "INSUFFICIENT",
    "unmet_requirement": "a cited record of the credential-dumping process",
    "reason": "Fake LLM backend (test profile): INSUFFICIENT because no cited record names a "
    "dumping tool, not a model judgement.",
}


def _proposal(tool: str, args: dict[str, Any], cited: list[int], thought: str) -> dict[str, Any]:
    return {"thought": f"Fake LLM backend (test profile): {thought}", "tool": tool, "args": args,
            "cited": cited, "technique_id": "T1003.001" if cited else None}  # fmt: skip


# The Fake profile's agent (J5 on the e2e fixture's mini scenario, E2): it cites E2's benign
# records (6, 12), gets INSUFFICIENT, runs one recovery query for lsass access, then cites the
# dumper's accesses that query returns (5, 7) and is admitted. Any other gate feedback ends the
# episode.
FAKE_HOST = "WKSTN-01.lab.local"
RECOVERY_SQL = "SELECT * FROM process_access WHERE target_image LIKE '%lsass%' LIMIT 10"


def fake_rules() -> list[FakeRule]:
    """Rules in order (the first match answers). Verifier prompts contain ``CITED_RECORDS``; the
    proposer's system prompt starts with "You are a SOC responder"."""
    # the CITED_RECORDS block itself (the system prompt also names it): header, then the records
    no_tool = (
        r"CITED_RECORDS\n<<UNTRUSTED_LOG_DATA>>(?:(?![Dd][Uu][Mm][Pp])"
        r"(?!<</UNTRUSTED_LOG_DATA>>)[\s\S])*<</UNTRUSTED_LOG_DATA>>"
    )
    investigator = [  # Exp 3 investigator prompts (mini fixture): verifier first, then SQL
        FakeRule([{"thought": "Fake LLM backend (test profile)", "hypotheses": [
            {"technique_id": "T1003.001", "behaviour": "a process opens lsass.exe memory"},
            {"technique_id": "T1059.001", "behaviour": "powershell runs an encoded command"},
            {"technique_id": "T1547.001", "behaviour": "a run key starts a program at logon"}]}],
                 pattern="WINDOW PROFILE"),
        FakeRule([{"verdict": "ACCEPT", "cited": [5, 7],
                   "reason": "Fake LLM backend (test profile): fixed ACCEPT"}],
                 pattern=r"HYPOTHESIS\nT1003\.001[\s\S]*QUERY RESULTS"),
        FakeRule([{"verdict": "REJECT", "cited": [],
                   "reason": "Fake LLM backend (test profile): fixed REJECT"}],
                 pattern=r"HYPOTHESIS\n[\s\S]*QUERY RESULTS"),
        FakeRule([{"queries": [{"sql": RECOVERY_SQL, "purpose": "lsass access"}]}],
                 pattern=r"HYPOTHESIS\n"),
    ]  # fmt: skip
    return [
        *investigator,
        FakeRule([FAKE_INSUFFICIENT], pattern=no_tool),
        FakeRule([FAKE_VERIFIER_OUTPUT], pattern="CITED_RECORDS"),
        FakeRule([_proposal("isolate_host", {"host": FAKE_HOST}, [5, 7],
                            "the recovery query returned the lsass access; re-citing it")],
                 pattern=r"SOC responder[\s\S]*target_image LIKE"),
        FakeRule([_proposal("sql_query", {"sql": RECOVERY_SQL}, [],
                            "the gate needs evidence of lsass access; querying for it")],
                 pattern=r"SOC responder[\s\S]*C4_INSUFFICIENT"),
        FakeRule([_proposal("end_episode", {"reason": "cannot_proceed"}, [],
                            "the gate did not admit the call; ending")],
                 pattern=r"SOC responder[\s\S]*(?:C[0-9]_[A-Z_]+:|APPROVAL_|Refused|Denied|"
                         r"No response|mock action recorded|RECOVERY_BUDGET)"),
        FakeRule([_proposal("isolate_host", {"host": FAKE_HOST}, [6, 12],
                            "containing the host on the records in the prefix")],
                 pattern="SOC responder"),
        FakeRule([FAKE_VERIFIER_OUTPUT]),
    ]  # fmt: skip


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
                "backend_flags": {"delay_ms": settings.fake_llm_delay_ms,
                                  "recording": str(settings.record_cassette)
                                  if settings.record_cassette else None}}  # fmt: skip
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
                          if settings.llm_backend == "replay" else None,
                          "recording": str(settings.record_cassette)
                          if settings.record_cassette else None},
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
    client = _make_client(settings)
    if settings.record_cassette is not None and settings.llm_backend != "replay":
        from gbya.llm.replay import RecordingLLMClient

        return RecordingLLMClient(client, settings.resolve(settings.record_cassette))
    return client


def _make_client(settings: Settings) -> LLMClient:
    if settings.llm_backend == "fake":
        return _DelayedFake(settings.fake_llm_delay_ms, fake_rules())
    if settings.llm_backend == "replay":
        from gbya.llm.replay import ReplayLLMClient

        if settings.replay_cassette is None:
            raise ValueError("llm_backend=replay needs replay_cassette")
        return ReplayLLMClient(settings.resolve(settings.replay_cassette))
    from gbya.llm.live import LiveLLMClient

    return LiveLLMClient(settings.model_base_url, str(_profile()["served_model_name"]))
