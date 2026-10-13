"""Decision C (13 Oct 2026): after the two M6.1 development rounds the investigator prompts are
frozen. Changing one needs a team decision and a new hash here and in STATUS.md. The proposer
prompt (shared by Exp 2 and Exp 3) was changed in round 2 and is pinned too."""

from __future__ import annotations

import hashlib

import pytest

from gbya.agent.investigator import PROMPTS

FROZEN = {
    "hypothesise.md": "bbf2a825aca12d614e1f58b55dea2ff6072414ff53b3801d8a1f54f91a20a48a",
    "sqlgen.md": "aee9ef5f26f67ed1dd489d7f7ac22cc2cc917c6c4eb5144b86965a5b9f331667",
    "technique_verifier.md": "d4927a2a0d3fc1c34f31774fce364ace675f31b8c6cb06966411a791b27ae186",
    "proposer.md": "c9d5e5405bcc47f7c39d0b026ac4454cceeab9f75308c7476212060c36106cf8",
}


@pytest.mark.parametrize("name", sorted(FROZEN))
def test_prompt_is_frozen(name: str) -> None:
    digest = hashlib.sha256((PROMPTS / name).read_bytes()).hexdigest()
    assert digest == FROZEN[name], f"{name} changed after the M6.1 freeze (team decision needed)"
