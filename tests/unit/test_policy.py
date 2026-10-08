"""T2.2: policy engine (C5) — every rule of policy/rules.yaml plus the default; rule-file checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gbya.config import REPO_ROOT
from gbya.context.models import TrustedContext
from gbya.policy.engine import PolicyEngine, PolicyFile, load_evidence_requirements

CTX = TrustedContext.model_validate(
    {
        "schema_version": 1,
        "assets": [
            {"host": "wkstn-01", "role": "workstation", "tier": 2},
            {"host": "app-01", "role": "server", "tier": 1},
            {"host": "dc-01", "role": "domain_controller", "tier": 0},
        ],
        "identities": [
            {"account": "svc_reports", "type": "service", "privilege": "standard",
             "dependents": ["nightly-reporting"]},
            {"account": "svc_idle", "type": "service", "privilege": "standard", "dependents": []},
            {"account": "a.mehta", "type": "human", "privilege": "standard"},
            {"account": "it.admin", "type": "human", "privilege": "admin"},
            {"account": "da.root", "type": "human", "privilege": "domain_admin"},
            {"account": "svc_backup", "type": "service", "privilege": "admin"},
            {"account": "h.deps", "type": "human", "privilege": "standard", "dependents": ["payroll-export"]},
        ],
        "network": {"internal_cidrs": ["10.0.0.0/8"]},
        "approval_script": {"mode": "grant"},
    }
)  # fmt: skip
ENGINE = PolicyEngine.from_file(REPO_ROOT / "policy" / "rules.yaml")


@pytest.mark.parametrize(
    ("tool", "args", "decision", "rule"),
    [
        ("isolate_host", {"host": "dc-01"}, "needs_approval", "P1-tier0-isolate"),
        ("isolate_host", {"host": "wkstn-01"}, "allowed", "P2-tier12-isolate"),
        ("isolate_host", {"host": "APP-01"}, "allowed", "P2-tier12-isolate"),  # case-insensitive
        ("disable_account", {"account": "svc_reports"}, "needs_approval", "P3-account-dependents"),
        ("disable_account", {"account": "LAB\\a.mehta"}, "allowed", "P4-human-account"),
        ("disable_account", {"account": "h.deps"}, "needs_approval", "P3-account-dependents"),  # human + dependents
        ("disable_account", {"account": "it.admin"}, "needs_approval", "P5-privileged-account"),
        ("disable_account", {"account": "da.root"}, "needs_approval", "P5-privileged-account"),
        ("disable_account", {"account": "svc_backup"}, "needs_approval", "P5-privileged-account"),
        ("kill_process", {"host": "wkstn-01", "pid": 4100}, "allowed", "P6-irreversible-tier2"),
        ("kill_process", {"host": "app-01", "pid": 4100}, "needs_approval", "P7-irreversible-tier01"),
        ("kill_process", {"host": "dc-01", "pid": 4100}, "needs_approval", "P7-irreversible-tier01"),
        ("block_ip", {"ip": "203.0.113.7"}, "allowed", "P8-block-external-ip"),
        ("disable_account", {"account": "svc_idle"}, "allowed", "P9-svc-account-no-dependents"),
        # Default-deny: no rule matches.
        ("disable_account", {"account": "nobody"}, "forbidden", "default"),  # unknown account
        ("isolate_host", {"host": "unknown-host"}, "forbidden", "default"),  # unknown host
    ],
)  # fmt: skip
def test_every_rule_and_the_default(
    tool: str, args: dict[str, Any], decision: str, rule: str
) -> None:
    d = ENGINE.evaluate(tool, args, CTX)
    assert (d.decision, d.rule_id) == (decision, rule)


def test_every_rule_is_exercised() -> None:
    ids = {r.id for r in ENGINE.policy.rules}
    assert ids == {f"P{i}" + s for i, s in [
        (1, "-tier0-isolate"), (2, "-tier12-isolate"), (3, "-account-dependents"),
        (4, "-human-account"), (5, "-privileged-account"), (6, "-irreversible-tier2"),
        (7, "-irreversible-tier01"), (8, "-block-external-ip"), (9, "-svc-account-no-dependents")]}  # fmt: skip
    assert ENGINE.policy.default == "forbidden"


def _policy(
    rules: list[dict[str, Any]], default: str = "forbidden", tools: Any = None
) -> PolicyEngine:
    doc: dict[str, Any] = {"version": 1, "default": default, "rules": rules}
    if tools:
        doc["tools"] = tools
    return PolicyEngine(PolicyFile.model_validate(doc))


def test_strictest_matching_decision_wins() -> None:
    eng = _policy(
        [
            {"id": "A", "when": {"tool": "isolate_host", "host.tier": [0, 1, 2]}, "decision": "allowed"},
            {"id": "B", "when": {"tool": "isolate_host", "host.tier": 0}, "decision": "needs_approval"},
            {"id": "C", "when": {"tool": "isolate_host", "host.role": "domain_controller"}, "decision": "forbidden"},
        ]
    )  # fmt: skip
    # dc-01 (tier 0, domain controller) matches A, B and C: forbidden wins regardless of order
    assert eng.evaluate("isolate_host", {"host": "dc-01"}, CTX).rule_id == "C"
    # app-01 (tier 1) matches only A
    assert eng.evaluate("isolate_host", {"host": "app-01"}, CTX).rule_id == "A"
    reversed_eng = _policy(list(reversed(eng.policy.model_dump()["rules"])))
    assert reversed_eng.evaluate("isolate_host", {"host": "dc-01"}, CTX).decision == "forbidden"


def test_equally_strict_matches_report_the_first_rule() -> None:
    eng = _policy(
        [
            {"id": "FIRST", "when": {"tool": "disable_account", "account.type": "service"}, "decision": "needs_approval"},
            {"id": "SECOND", "when": {"tool": "disable_account", "account.privilege": "admin"}, "decision": "needs_approval"},
        ]
    )  # fmt: skip
    d = eng.evaluate("disable_account", {"account": "svc_backup"}, CTX)
    assert (d.decision, d.rule_id) == ("needs_approval", "FIRST")
    assert [
        r.id for r in eng.matching_rules("disable_account", {"account": "svc_backup"}, CTX)
    ] == ["FIRST", "SECOND"]


def test_rules_file_overlaps_resolve_by_strictness() -> None:
    """A service admin with dependents matches P3 and P5 (both needs_approval): P3 is reported."""
    ctx = TrustedContext.model_validate(
        {**CTX.model_dump(mode="json"), "identities": [
            {"account": "svc_sql", "type": "service", "privilege": "admin", "dependents": ["db"]}]}
    )  # fmt: skip
    args = {"account": "svc_sql"}
    assert [r.id for r in ENGINE.matching_rules("disable_account", args, ctx)] == [
        "P3-account-dependents",
        "P5-privileged-account",
    ]
    d = ENGINE.evaluate("disable_account", args, ctx)
    assert (d.decision, d.rule_id) == ("needs_approval", "P3-account-dependents")


def test_rule_without_tool_applies_to_every_tool() -> None:
    eng = _policy(
        [{"id": "T0", "when": {"host.tier": 0}, "decision": "needs_approval"}], default="allowed"
    )
    assert eng.evaluate("isolate_host", {"host": "dc-01"}, CTX).rule_id == "T0"
    assert eng.evaluate("kill_process", {"host": "dc-01", "pid": 1}, CTX).rule_id == "T0"
    assert (
        eng.evaluate("disable_account", {"account": "a.mehta"}, CTX).rule_id == "default"
    )  # no host


def test_action_attributes_and_nonempty() -> None:
    policy = PolicyFile.model_validate(
        {
            "version": 1,
            "default": "allowed",
            "tools": {"kill_process": {"reversible": False}, "isolate_host": {"reversible": True}},
            "rules": [
                {"id": "IRREV", "when": {"tool": ["kill_process", "isolate_host"],
                                         "action.reversible": False}, "decision": "needs_approval"},
                {"id": "DEP", "when": {"tool": "disable_account", "account.dependents": "nonempty"},
                 "decision": "forbidden"},
                {"id": "NODEP", "when": {"tool": "disable_account", "account.dependents": "empty",
                                         "account.type": "service"}, "decision": "needs_approval"},
            ],
        }
    )  # fmt: skip
    eng = PolicyEngine(policy)
    assert eng.evaluate("kill_process", {"host": "wkstn-01", "pid": 1}, CTX).rule_id == "IRREV"
    assert eng.evaluate("isolate_host", {"host": "wkstn-01"}, CTX).rule_id == "default"
    assert eng.evaluate("disable_account", {"account": "svc_reports"}, CTX).rule_id == "DEP"
    assert eng.evaluate("disable_account", {"account": "svc_idle"}, CTX).rule_id == "NODEP"
    assert (
        eng.evaluate("disable_account", {"account": "a.mehta"}, CTX).rule_id == "default"
    )  # human, empty


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ([{"id": "X", "when": {}, "decision": "allowed"}], "at least one condition"),
        ([{"id": "X", "when": {"tool": "delete_logs"}, "decision": "allowed"}], "not a state-changing"),
        ([{"id": "X", "when": {"tool": "isolate_host", "host.colour": "red"}, "decision": "allowed"}],
         "unknown attribute"),
        ([{"id": "X", "when": {"tool": "isolate_host", "log.user": "x"}, "decision": "allowed"}],
         "unknown attribute"),
        ([{"id": "X", "when": {"tool": "isolate_host", "action.reversible": True}, "decision": "allowed"}],
         "unknown attribute"),  # no tools metadata declared
        ([{"id": "X", "when": {"tool": "block_ip"}, "decision": "allowed"},
          {"id": "X", "when": {"tool": "block_ip"}, "decision": "forbidden"}], "unique"),
        ([{"id": "X", "when": {"tool": "block_ip"}, "decision": "maybe"}], "decision"),
    ],
)  # fmt: skip
def test_rule_file_validation(rules: list[dict[str, Any]], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PolicyFile.model_validate({"version": 1, "default": "forbidden", "rules": rules})


def test_c5_is_for_state_changing_tools_only() -> None:
    with pytest.raises(ValueError, match="state-changing"):
        ENGINE.evaluate("sql_query", {"sql": "SELECT 1"}, CTX)


def test_evidence_requirements_cover_every_tool(tmp_path: Path) -> None:
    reqs = load_evidence_requirements(REPO_ROOT / "policy" / "evidence_requirements.yaml")
    assert set(reqs) == {"isolate_host", "kill_process", "disable_account", "block_ip"}
    assert reqs["isolate_host"].startswith("At least one cited record on host H")
    bad = tmp_path / "e.yaml"
    bad.write_text("requirements:\n  isolate_host: x\n")
    with pytest.raises(ValueError, match="cover exactly"):
        load_evidence_requirements(bad)


def test_policy_table_is_up_to_date() -> None:
    """docs/policy_table.md must equal a fresh generation from the engine (`make policy-table`)."""
    from gbya.policy.table import OUT, generate, rows

    assert OUT.read_text() == generate(), "run `make policy-table` and commit docs/policy_table.md"
    grid = rows(ENGINE)
    assert len(grid) == 4 * 3 * 2 * 3 * 2
    assert [r for r in grid if r["rule"] == "default"] == []  # no fall-through to `forbidden`
