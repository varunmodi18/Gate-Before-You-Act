"""Per-window facts for case authors: acting users on a host (for ``disable_account`` cases).

C3 for ``disable_account(U)`` needs a cited record whose *acting-user* field equals U (plan
§D.6.2; the field per table is in ``gbya.data.fieldmap.acting_user_column``). This module reports,
for one host, which records name an acting user at all, so authors can see in advance which
windows can support a ``disable_account`` case.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import duckdb

from gbya.data.fieldmap import ACTING_USER, TABLES

# Built-in Windows identities: present in many logs, never a realistic account to disable.
BUILTIN_ACCOUNTS = frozenset({"system", "local service", "network service", "anonymous logon"})
BUILTIN_PREFIXES = ("dwm-", "umfd-")


def is_builtin(account: str) -> bool:
    a = account.lower()
    return a in BUILTIN_ACCOUNTS or a.endswith("$") or a.startswith(BUILTIN_PREFIXES)


@dataclass(frozen=True)
class ActingUsers:
    host: str
    records: int  # records on the host whose acting-user field is set
    by_table: dict[str, int] = field(default_factory=dict)
    accounts: dict[str, int] = field(default_factory=dict)  # account → records

    @property
    def non_builtin(self) -> dict[str, int]:
        return {a: n for a, n in self.accounts.items() if not is_builtin(a)}


def _acting_user_sql(table: str) -> str:
    if table == "logon":  # 4624/4625 → target_user; 4648 → subject_user (§F.2)
        return "CASE WHEN event_id = 4648 THEN subject_user ELSE target_user END"
    return f'"{ACTING_USER[table]}"'


def acting_users(con: duckdb.DuckDBPyConnection, host: str) -> ActingUsers:
    by_table: dict[str, int] = {}
    accounts: Counter[str] = Counter()
    for table in TABLES:
        rows = con.execute(
            f"SELECT acting, count(*) FROM (SELECT {_acting_user_sql(table)} AS acting "
            f'FROM "{table}" WHERE host = ?) WHERE acting IS NOT NULL GROUP BY acting',
            [host],
        ).fetchall()
        n = sum(int(c) for _, c in rows)
        if n:
            by_table[table] = n
        for acct, c in rows:
            accounts[str(acct)] += int(c)
    return ActingUsers(
        host=host,
        records=sum(by_table.values()),
        by_table=dict(sorted(by_table.items())),
        accounts=dict(sorted(accounts.items(), key=lambda kv: (-kv[1], kv[0]))),
    )
