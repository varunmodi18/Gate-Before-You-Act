"""T2.1: trusted-context model — every validation rule, hash stability, lookups, get_context."""

from __future__ import annotations

import copy
import ipaddress
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from gbya.context.models import SECTIONS, TrustedContext
from gbya.context.store import canonical_json, context_hash, get_context, load_context

BASE: dict[str, Any] = {
    "schema_version": 1,
    "assets": [
        {"host": "wkstn-01", "role": "workstation", "tier": 2, "owner": "finance"},
        {"host": "dc-01", "role": "domain_controller", "tier": 0, "owner": "it"},
    ],
    "identities": [
        {"account": "svc_reports", "type": "service", "privilege": "standard",
         "dependents": ["nightly-reporting"]},
        {"account": "a.mehta", "type": "human", "privilege": "standard", "dependents": []},
    ],
    "network": {"internal_cidrs": ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"],
                "protected_addresses": ["10.0.0.10"]},
    "approval_script": {"mode": "unreachable"},
    "change_tickets": [{"id": "CHG-DEMO-1", "host": "wkstn-01", "account": "a.mehta",
                        "command_pattern": "(?i)mimikatz|dumpert",
                        "start": "2020-10-18T01:00:00Z", "end": "2020-10-18T03:00:00Z",
                        "approved": True}],
}  # fmt: skip


def _with(path: str, value: Any) -> dict[str, Any]:
    doc = copy.deepcopy(BASE)
    target: Any = doc
    *parts, last = path.split(".")
    for p in parts:
        target = target[int(p)] if p.isdigit() else target[p]
    if value is _DELETE:
        del target[int(last) if last.isdigit() else last]
    else:
        target[int(last) if last.isdigit() else last] = value
    return doc


_DELETE = object()


def test_plan_example_is_valid() -> None:
    ctx = TrustedContext.model_validate(BASE)
    assert ctx.asset("WKSTN-01") is not None and ctx.asset("wkstn-02") is None
    assert ctx.identity("LAB\\A.Mehta") is not None  # same normalisation as log users


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("assets.0.tier", 3, "tier"),  # tier ∈ {0, 1, 2}
        ("assets.0.tier", _DELETE, "tier"),  # missing tier
        ("approval_script.mode", "maybe", "mode"),  # mode ∈ {unreachable, grant, deny}
        ("change_tickets.0.end", "2020-10-18T00:00:00Z", "start must be before end"),
        ("change_tickets.0.command_pattern", "(unclosed", "not a valid regex"),
        ("change_tickets.0.host", "unknown-host", "not in assets"),
        ("change_tickets.0.start", "2020-10-18T01:00:00", "time zone"),
        ("change_tickets.0.approved", _DELETE, "approved"),  # malformed ticket
        ("identities.0.type", "robot", "type"),
        ("identities.0.privilege", "root", "privilege"),
        ("network.internal_cidrs.0", "10.0.0.0/33", "internal_cidrs"),
        ("network.protected_addresses.0", "not-an-ip", "protected_addresses"),
        ("schema_version", 2, "schema_version"),
        ("assets", [], "assets"),
        ("assets.0.colour", "blue", "colour"),  # unknown field rejected
    ],
)
def test_validation_rejects(path: str, value: Any, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        TrustedContext.model_validate(_with(path, value))


@pytest.mark.parametrize(
    ("section", "dup", "message"),
    [
        ("assets", {"host": "WKSTN-01", "role": "x", "tier": 1}, "duplicate hosts"),
        ("identities", {"account": "LAB\\svc_reports", "type": "service", "privilege": "admin"},
         "duplicate accounts"),
        ("change_tickets", {**BASE["change_tickets"][0]}, "duplicate change-ticket ids"),
    ],
)  # fmt: skip
def test_duplicates_rejected(section: str, dup: dict[str, Any], message: str) -> None:
    doc = copy.deepcopy(BASE)
    doc[section].append(dup)
    with pytest.raises(ValidationError, match=message):
        TrustedContext.model_validate(doc)


def test_hash_is_stable_under_key_order_and_whitespace(tmp_path: Path) -> None:
    a = TrustedContext.model_validate(BASE)
    shuffled = json.loads(json.dumps(BASE, sort_keys=True))  # different key order
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps(shuffled, indent=4))
    b = load_context(p)
    assert context_hash(a) == context_hash(b)
    assert canonical_json(a) == canonical_json(b)
    assert len(context_hash(a)) == 64


def test_hash_changes_with_content() -> None:
    a = TrustedContext.model_validate(BASE)
    b = TrustedContext.model_validate(_with("assets.0.tier", 1))
    c = TrustedContext.model_validate(_with("change_tickets.0.account", "svc_reports"))
    assert len({context_hash(a), context_hash(b), context_hash(c)}) == 3


def test_get_context_sections() -> None:
    ctx = TrustedContext.model_validate(BASE)
    assert set(SECTIONS) == {"assets", "identities", "network", "approval_script", "change_tickets"}
    assert get_context(ctx, "assets")[1] == {
        "host": "dc-01", "role": "domain_controller", "tier": 0, "owner": "it",
    }  # fmt: skip
    assert get_context(ctx, "approval_script") == {"mode": "unreachable"}
    assert get_context(ctx, "change_tickets")[0]["start"] == "2020-10-18T01:00:00Z"
    with pytest.raises(ValueError, match="unknown context section"):
        get_context(ctx, "secrets")  # type: ignore[arg-type]


def test_network_and_ticket_helpers() -> None:
    ctx = TrustedContext.model_validate(BASE)
    assert ctx.network.is_internal(ipaddress.ip_address("10.1.2.3"))
    assert not ctx.network.is_internal(ipaddress.ip_address("203.0.113.7"))
    assert ctx.network.is_protected(ipaddress.ip_address("10.0.0.10"))
    t = ctx.change_tickets[0]
    assert t.window_utc() == (datetime(2020, 10, 18, 1), datetime(2020, 10, 18, 3))
    assert [x.id for x in ctx.tickets_for(host="WKSTN-01")] == ["CHG-DEMO-1"]
    assert [x.id for x in ctx.tickets_for(account="LAB\\a.mehta")] == ["CHG-DEMO-1"]
    assert ctx.tickets_for(host="dc-01", account="svc_reports") == []


def test_model_is_immutable() -> None:
    ctx = TrustedContext.model_validate(BASE)
    with pytest.raises(ValidationError):
        ctx.assets[0].tier = 0  # type: ignore[misc]
