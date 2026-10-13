"""Decision C (13 Oct 2026) and the team lead's round-3 decision (14 Oct 2026): after the third and
final M6.1 round the investigator prompts and output schemas are frozen and are not reopened.
The proposer prompt (shared by Exp 2 and Exp 3) was changed in round 2 only and is pinned too, so
Exp 2 cannot change again unnoticed. A change here needs a team decision and new hashes in
STATUS.md."""

from __future__ import annotations

import hashlib
import json

import pytest

from gbya.agent import investigator as iv

PROMPTS = {
    "hypothesise.md": "da50e223d22069b793f41b951ce378608e42b76fc57190109471d6398ff6c151",
    "sqlgen.md": "aee9ef5f26f67ed1dd489d7f7ac22cc2cc917c6c4eb5144b86965a5b9f331667",
    "technique_verifier.md": "d4927a2a0d3fc1c34f31774fce364ace675f31b8c6cb06966411a791b27ae186",
    "proposer.md": "c9d5e5405bcc47f7c39d0b026ac4454cceeab9f75308c7476212060c36106cf8",
}
SCHEMAS = {
    "HYPOTHESES_SCHEMA": "9d947989cad9e1da073100cb4fffc7cc2e56692fede9a8f0e2f16fcf0ebd5c47",
    "SQL_SCHEMA": "8326cb63aab6cc4e1fd643292f2590ccb334d9dd10a737c6a0e20c8b375d9fa8",
    "VERDICT_SCHEMA": "4ed3a06aa6c84cebe77fb84fbb427f457025a70dd929e36d9c96d637a15fd8fa",
}


@pytest.mark.parametrize("name", sorted(PROMPTS))
def test_prompt_is_frozen(name: str) -> None:
    digest = hashlib.sha256((iv.PROMPTS / name).read_bytes()).hexdigest()
    assert digest == PROMPTS[name], f"{name} changed after the M6.1 freeze (team decision needed)"


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_schema_is_frozen(name: str) -> None:
    text = json.dumps(getattr(iv, name), sort_keys=True).encode()
    assert hashlib.sha256(text).hexdigest() == SCHEMAS[name], f"{name} changed after the freeze"


def test_investigator_limits_are_frozen() -> None:
    assert (iv.MIN_HYPOTHESES, iv.MAX_HYPOTHESES, iv.MAX_QUERIES, iv.MAX_LLM_CALLS,
            iv.HANDOVER_TOKENS, iv.CANDIDATES) == (3, 5, 2, 25, 2500, 10)  # fmt: skip
