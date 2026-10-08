"""Trusted context (plan §F.3, §D.4, FR-06, T2.1).

Written by annotators per case; the only source of facts that decide safety (asset tiers,
accounts, approval behaviour, change tickets, network ranges). Never derived from logs.

Validation (§F.3): tier ∈ {0, 1, 2}; approval mode ∈ {unreachable, grant, deny}; ticket
``start < end``; ``command_pattern`` compiles; ticket hosts exist in ``assets``. Additionally
(T2.1): unknown fields are rejected; account ``type``/``privilege`` are the values the policy rules
use; host names, accounts and ticket ids are unique, so every lookup is unambiguous.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def host_key(host: str) -> str:
    """Host names compare case-insensitively (Windows/DNS names)."""
    return host.strip().casefold()


def account_key(account: str) -> str:
    """Accounts compare like normalised log users: ``DOMAIN\\`` stripped, lower-case."""
    return account.split("\\")[-1].strip().lower()


class Asset(_Strict):
    host: str = Field(min_length=1)
    role: str = Field(min_length=1)
    tier: Literal[0, 1, 2]
    owner: str | None = None


class Identity(_Strict):
    account: str = Field(min_length=1)
    type: Literal["human", "service"]
    privilege: Literal["standard", "admin", "domain_admin"]
    dependents: list[str] = Field(default_factory=list)


class Network(_Strict):
    internal_cidrs: list[IPNetwork]
    protected_addresses: list[IPAddress] = Field(default_factory=list)

    def is_internal(self, ip: IPAddress) -> bool:
        return any(ip in net for net in self.internal_cidrs)

    def is_protected(self, ip: IPAddress) -> bool:
        return ip in self.protected_addresses


class ApprovalScript(_Strict):
    mode: Literal["unreachable", "grant", "deny"]


class ChangeTicket(_Strict):
    id: str = Field(min_length=1)
    host: str = Field(min_length=1)
    account: str = Field(min_length=1)
    command_pattern: str
    start: datetime
    end: datetime
    approved: bool

    @field_validator("command_pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as exc:
            raise ValueError(f"command_pattern is not a valid regex: {exc}") from exc
        return v

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ticket times need a time zone (e.g. ...Z)")
        return v

    @model_validator(mode="after")
    def _ordered(self) -> ChangeTicket:
        if not self.start < self.end:
            raise ValueError(f"ticket {self.id}: start must be before end")
        return self

    def window_utc(self) -> tuple[datetime, datetime]:
        """Start and end as naive UTC, comparable with log ``ts`` values written in UTC."""
        return (
            self.start.astimezone(UTC).replace(tzinfo=None),
            self.end.astimezone(UTC).replace(tzinfo=None),
        )


Section = Literal["assets", "identities", "network", "approval_script", "change_tickets"]
SECTIONS: tuple[Section, ...] = (
    "assets",
    "identities",
    "network",
    "approval_script",
    "change_tickets",
)


class TrustedContext(_Strict):
    schema_version: Literal[1]
    assets: list[Asset] = Field(min_length=1)
    identities: list[Identity] = Field(default_factory=list)
    network: Network
    approval_script: ApprovalScript
    change_tickets: list[ChangeTicket] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> TrustedContext:
        def dupes(keys: list[str]) -> list[str]:
            return sorted(k for k, n in Counter(keys).items() if n > 1)

        if d := dupes([host_key(a.host) for a in self.assets]):
            raise ValueError(f"duplicate hosts in assets: {d}")
        if d := dupes([account_key(i.account) for i in self.identities]):
            raise ValueError(f"duplicate accounts in identities: {d}")
        if d := dupes([t.id for t in self.change_tickets]):
            raise ValueError(f"duplicate change-ticket ids: {d}")
        known = {host_key(a.host) for a in self.assets}
        for t in self.change_tickets:
            if host_key(t.host) not in known:
                raise ValueError(f"ticket {t.id}: host {t.host} is not in assets")
        return self

    def asset(self, host: str) -> Asset | None:
        key = host_key(host)
        return next((a for a in self.assets if host_key(a.host) == key), None)

    def identity(self, account: str) -> Identity | None:
        key = account_key(account)
        return next((i for i in self.identities if account_key(i.account) == key), None)

    def tickets_for(
        self, host: str | None = None, account: str | None = None
    ) -> list[ChangeTicket]:
        """Tickets whose host or account matches (the verifier's CHANGE_TICKETS block, §D.7)."""
        hk = host_key(host) if host else None
        ak = account_key(account) if account else None
        return [
            t
            for t in self.change_tickets
            if (hk is not None and host_key(t.host) == hk)
            or (ak is not None and account_key(t.account) == ak)
        ]
