"""Policy engine for check C5 (plan §D.8, FR-07, T2.2).

``policy/rules.yaml`` lists rules keyed by action type and trusted attributes. Every rule whose
conditions all hold is a match; **the strictest matching decision wins** (``forbidden`` >
``needs_approval`` > ``allowed``), and among equally strict matches the first in file order is
reported. With no match the file's ``default`` applies (``forbidden``: default-deny). A rule may
omit ``tool`` (it then applies to every state-changing tool) but needs at least one condition.
Attributes come only from trusted context, never from logs:

* ``host.<field>``    — the asset named by the call's ``host`` argument (tier, role, owner);
* ``account.<field>`` — the identity named by ``account`` (type, privilege, dependents);
* ``action.<field>``  — per-tool facts declared in the rules file's ``tools`` section
  (e.g. ``reversible``; proposal §8 lists reversibility among the policy attributes).

Condition values: a scalar means equality (strings case-insensitive), a list means membership,
``nonempty`` means a non-empty list or string, and ``empty`` an empty one. If the named host
or account is not in the trusted context, its conditions are false (C1 rejects such calls first).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from gbya.context.models import Asset, Identity, TrustedContext
from gbya.tools.names import STATE_CHANGING

Decision = Literal["allowed", "needs_approval", "forbidden"]
NONEMPTY = "nonempty"
EMPTY = "empty"
STRICTNESS: dict[str, int] = {"allowed": 0, "needs_approval": 1, "forbidden": 2}
NAMESPACES = {"host": set(Asset.model_fields), "account": set(Identity.model_fields)}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1)
    when: dict[str, Any]
    decision: Decision


class PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1]
    default: Decision
    tools: dict[str, dict[str, Any]] = Field(default_factory=dict)
    rules: list[Rule]

    @model_validator(mode="after")
    def _check(self) -> PolicyFile:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("rule ids must be unique")
        unknown_tools = set(self.tools) - set(STATE_CHANGING)
        if unknown_tools:
            raise ValueError(f"tools section names unknown tools: {sorted(unknown_tools)}")
        action_fields = {k for meta in self.tools.values() for k in meta}
        for r in self.rules:
            if not r.when:
                raise ValueError(f"rule {r.id}: 'when' needs at least one condition")
            raw = r.when.get("tool", list(STATE_CHANGING))
            tools = raw if isinstance(raw, list) else [raw]
            if bad := [t for t in tools if t not in STATE_CHANGING]:
                raise ValueError(f"rule {r.id}: not a state-changing tool: {bad}")
            for key in r.when:
                if key == "tool":
                    continue
                ns, _, attr = key.partition(".")
                allowed = NAMESPACES.get(ns, action_fields if ns == "action" else None)
                if not attr or allowed is None or attr not in allowed:
                    raise ValueError(f"rule {r.id}: unknown attribute {key!r}")
        return self


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision: Decision
    rule_id: str  # the matching rule, or "default"


def _norm(v: Any) -> Any:
    return v.casefold() if isinstance(v, str) else v


def _holds(expected: Any, actual: Any) -> bool:
    if actual is None:
        return False
    if expected == NONEMPTY:
        return isinstance(actual, list | str) and len(actual) > 0
    if expected == EMPTY:
        return isinstance(actual, list | str) and len(actual) == 0
    values = actual if isinstance(actual, list) else [actual]
    wanted = expected if isinstance(expected, list) else [expected]
    return any(_norm(a) == _norm(w) for a in values for w in wanted)


class PolicyEngine:
    def __init__(self, policy: PolicyFile) -> None:
        self.policy = policy

    @classmethod
    def from_file(cls, path: Path) -> PolicyEngine:
        return cls(PolicyFile.model_validate(yaml.safe_load(path.read_text())))

    def _attribute(self, key: str, tool: str, args: dict[str, Any], ctx: TrustedContext) -> Any:
        ns, _, attr = key.partition(".")
        if ns == "host":
            asset = ctx.asset(str(args["host"])) if args.get("host") else None
            return getattr(asset, attr) if asset else None
        if ns == "account":
            ident = ctx.identity(str(args["account"])) if args.get("account") else None
            return getattr(ident, attr) if ident else None
        return self.policy.tools.get(tool, {}).get(attr)  # action.*

    def evaluate(self, tool: str, args: dict[str, Any], ctx: TrustedContext) -> PolicyDecision:
        if tool not in STATE_CHANGING:
            raise ValueError(f"C5 applies to state-changing tools only, not {tool!r}")
        matches = self.matching_rules(tool, args, ctx)
        if not matches:
            return PolicyDecision(decision=self.policy.default, rule_id="default")
        # strictest decision wins; ties keep file order (max() returns the first maximum)
        best = max(matches, key=lambda r: STRICTNESS[r.decision])
        return PolicyDecision(decision=best.decision, rule_id=best.id)

    def matching_rules(self, tool: str, args: dict[str, Any], ctx: TrustedContext) -> list[Rule]:
        """Every rule whose conditions all hold, in file order."""
        return [
            rule
            for rule in self.policy.rules
            if _holds(rule.when.get("tool", tool), tool)
            and all(
                _holds(expected, self._attribute(key, tool, args, ctx))
                for key, expected in rule.when.items()
                if key != "tool"
            )
        ]


def load_evidence_requirements(path: Path) -> dict[str, str]:
    """``policy/evidence_requirements.yaml``: one plain-language requirement per tool (for C4)."""
    data = yaml.safe_load(path.read_text())
    reqs = data.get("requirements") if isinstance(data, dict) else None
    if not isinstance(reqs, dict) or set(reqs) != set(STATE_CHANGING):
        raise ValueError(f"evidence requirements must cover exactly {STATE_CHANGING}")
    if not all(isinstance(v, str) and v.strip() for v in reqs.values()):
        raise ValueError("every evidence requirement must be non-empty text")
    return {k: " ".join(str(v).split()) for k, v in reqs.items()}
