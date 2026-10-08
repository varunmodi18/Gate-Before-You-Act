"""Gold rule sets from Sigma technique tags — for retrieval **scoring only** (plan §D.3).

The gold map is read from ``gold_map.json`` and is never passed to ``retrieve``, the verifier,
the gate or the agent. ``technique_gold`` comes from the case labels.

Gold set G for a case with technique ``T``:
* ``T`` is a sub-technique (``T1003.001``): rules tagged ``T``; if there are none, rules tagged
  its parent (``T1003``) — the plan's "parent technique if no sub-technique rule exists".
* ``T`` is a technique (``T1003``): rules tagged ``T`` or any of its sub-techniques
  (``T1003.xxx``), since each of those is also a ``T`` rule in the ATT&CK hierarchy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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
