"""Typed identifier arguments (plan §D.6.2 C1, T2.4).

Patterns from T2.4: hostname ``^[A-Za-z0-9][A-Za-z0-9\\-\\.]{0,62}$``; account
``^[A-Za-z0-9_.\\-$]{1,64}$``; PID a positive integer; IP parsed by ``ipaddress``; hash SHA-256 hex.
Log text can therefore only ever fill these slots with a value of the right shape; free text,
paths or instructions cannot pass.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

HOSTNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9\-\.]{0,62}$"
ACCOUNT_PATTERN = r"^[A-Za-z0-9_.\-$]{1,64}$"
SHA256_PATTERN = r"^[A-Fa-f0-9]{64}$"


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value.strip()))  # canonical form
    except ValueError as exc:
        raise ValueError(f"not an IP address: {value!r}") from exc


HostName = Annotated[str, StringConstraints(pattern=HOSTNAME_PATTERN)]
AccountName = Annotated[str, StringConstraints(pattern=ACCOUNT_PATTERN)]
Pid = Annotated[int, Field(gt=0, strict=True)]
IpAddress = Annotated[str, AfterValidator(_ip)]
Sha256 = Annotated[str, StringConstraints(pattern=SHA256_PATTERN)]
