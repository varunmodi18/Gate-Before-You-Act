"""Gold rule sets from Sigma technique tags — for retrieval **scoring only** (plan §D.3).

The gold map is read from ``gold_map.json`` and is never passed to ``retrieve``, the verifier,
the gate or the agent. ``technique_gold`` comes from the case labels.

Gold set G for a case with technique ``T``:
* ``T`` is a sub-technique (``T1003.001``): rules tagged ``T``; if there are none, rules tagged
  its parent (``T1003``) — the plan's "parent technique if no sub-technique rule exists".
* ``T`` is a technique (``T1003``): rules tagged ``T`` or any of its sub-techniques
  (``T1003.xxx``), since each of those is also a ``T`` rule in the ATT&CK hierarchy.

``revoked_by`` (exploratory view, team lead 2026-11-08): a ``technique_gold`` that the pinned
ATT&CK bundle marks revoked is mapped to its replacement through the bundle's own ``revoked-by``
relationships, and nothing else (no hand mapping). The registered metrics keep the label as is.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MAX_REVOCATION_HOPS = 5


def _external_id(obj: dict[str, object]) -> str | None:
    for ref in obj.get("external_references") or []:  # type: ignore[attr-defined]
        if isinstance(ref, dict) and ref.get("source_name") == "mitre-attack":
            return str(ref.get("external_id"))
    return None


def revoked_by(bundle: Path, expected_sha256: str) -> dict[str, str]:
    """Technique ID → replacement technique ID for every revoked attack-pattern, from the bundle's
    ``revoked-by`` relationships (followed to a technique that is not revoked). Refuses a bundle
    whose SHA-256 is not the pinned one."""
    raw = bundle.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"{bundle.name}: sha256 {digest} != pinned {expected_sha256}")
    objects = json.loads(raw)["objects"]
    patterns = {o["id"]: o for o in objects if o.get("type") == "attack-pattern"}
    target = {
        o["source_ref"]: o["target_ref"]
        for o in objects
        if o.get("type") == "relationship" and o.get("relationship_type") == "revoked-by"
        and not o.get("revoked") and o.get("source_ref") in patterns
    }  # fmt: skip
    out: dict[str, str] = {}
    for sid, obj in patterns.items():
        if not obj.get("revoked") or sid not in target or (tid := _external_id(obj)) is None:
            continue
        cur = target[sid]
        for _ in range(MAX_REVOCATION_HOPS):
            if not patterns.get(cur, {}).get("revoked") or cur not in target:
                break
            cur = target[cur]
        new = _external_id(patterns[cur]) if cur in patterns else None
        if new is not None and new != tid:
            out[tid] = new
    return out


def parent(technique: str) -> str:
    return technique.split(".", 1)[0]


@dataclass(frozen=True)
class GoldMap:
    rule_techniques: dict[str, list[str]]  # Sigma rule id → technique tags
    attack_techniques: dict[str, str]  # ATT&CK STIX id → technique id

    @classmethod
    def load(cls, index_dir: Path) -> GoldMap:
        data = json.loads((index_dir / "gold_map.json").read_text())
        return cls(data["sigma_rule_techniques"], data["attack_techniques"])

    def rules_tagged(self, technique: str, *, with_subs: bool = False) -> set[str]:
        def hit(t: str) -> bool:
            return t == technique or (with_subs and parent(t) == technique and "." in t)

        return {rid for rid, tags in self.rule_techniques.items() if any(hit(t) for t in tags)}

    def gold_rules(self, technique_gold: str) -> set[str]:
        if "." in technique_gold:
            return self.rules_tagged(technique_gold) or self.rules_tagged(parent(technique_gold))
        return self.rules_tagged(technique_gold, with_subs=True)

    def attack_correct(self, doc_id: str | None, technique_gold: str) -> bool:
        """ATT&CK top-1: the retrieved document is ``technique_gold`` or its parent."""
        if doc_id is None:
            return False
        got = self.attack_techniques.get(doc_id)
        return got is not None and got in (technique_gold, parent(technique_gold))
