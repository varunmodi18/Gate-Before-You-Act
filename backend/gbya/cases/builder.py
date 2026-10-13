"""Variant builder: scenario.json → the seven case files (plan §D.11, T4.3).

Pure: it reads the window database through the read-only execution path (for E2 sampling and the
next record id) and returns ``CaseFile`` objects. Writing per-case databases is the patcher's job
(construction path, ``gbya.cases.cli``); this module never imports it, so the API may use it.

| Variant | Package | Patch |
|---|---|---|
| E1 | as authored | — |
| E2 | the cited IDs replaced by as many benign records, seeded; by default on the target host so
  C3 alone cannot reject them. Never sampled: suspicious or cited records, records whose acting
  process is a process of a cited record or its parent or child (``processes.py``), and records
  annotators flagged as attack-related (each flag replaces just that record) | — |
| E3 | as authored (the malicious records now sit on host Y) | ``move_host`` |
| E4 | partial chain: the cited records that remain; contradiction: E1's citations **plus the added
  record** | ``remove`` / ``add`` |
| E5 | as authored | the authored in-scope edit |
| R_pos / R_neg | as authored | — (``r_edit``: tier, approval script or toolset) |

Labels are carried over from existing case files; the builder adds only ``e4_kind`` (the authored
E4 kind, not a judgement). Missing E3/E4/E5/Set R specs mean those variants are not generated;
the validator reports them.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from gbya.cases.models import (
    CaseFile,
    DbPatch,
    Labels,
    MoveHost,
    Remove,
    ScenarioFile,
    Variant,
)
from gbya.context.models import host_key
from gbya.data.connection import open_case_db
from gbya.data.fieldmap import TABLES


class BuildError(ValueError):
    pass


@dataclass(frozen=True)
class WindowFacts:
    """What the builder needs from the window database."""

    max_record_id: int
    hosts: dict[int, str | None]  # record id → host (normalised table, else raw event)
    attack_related: frozenset[int] = frozenset()  # acting process tied to the cited records


def window_facts(db: Path, cited: Sequence[int] = ()) -> WindowFacts:
    import json

    from gbya.cases.processes import attack_related_records

    con = open_case_db(db)
    try:
        hosts: dict[int, str | None] = {}
        for rid, raw in con.execute("SELECT record_id, json FROM raw_events").fetchall():
            ev = json.loads(raw)
            hosts[int(rid)] = ev.get("Hostname") or ev.get("Computer") or ev.get("computer_name")
        for t in TABLES:
            for rid, host in con.execute(f'SELECT record_id, host FROM "{t}"').fetchall():
                hosts[int(rid)] = host
        related = frozenset(attack_related_records(con, cited)) if cited else frozenset()
        return WindowFacts(max(hosts, default=0), hosts, related)
    finally:
        con.close()


def e2_order(sc: ScenarioFile, facts: WindowFacts) -> list[int]:
    """The seeded order in which E2 candidates are taken (flagged records not yet removed)."""
    excluded = set(sc.suspicious_record_ids) | set(sc.e1.cited) | set(facts.attack_related)
    pool = sorted(rid for rid, host in facts.hosts.items() if rid not in excluded and (
        not sc.e2.same_host or (host is not None and host_key(host) == host_key(sc.target_host))
    ))  # fmt: skip
    return random.Random(sc.e2.seed).sample(pool, len(pool))


def sample_e2(sc: ScenarioFile, facts: WindowFacts) -> list[int]:
    """The first k candidates of the seeded order that no annotator flagged: flagging a record
    replaces only that record, with the next candidate."""
    flagged = set(sc.e2.flagged_record_ids)
    order = [r for r in e2_order(sc, facts) if r not in flagged]
    k = len(sc.e1.cited)
    if len(order) < k:
        raise BuildError(f"E2 needs {k} benign records, only {len(order)} available")
    return sorted(order[:k])


def build_cases(
    sc: ScenarioFile, facts: WindowFacts, labels: Mapping[str, Labels] | None = None
) -> list[CaseFile]:
    labels = labels or {}
    e1 = sc.e1.model_dump(mode="json")

    def case(
        variant: Variant,
        *,
        cited: list[int] | None = None,
        patch: DbPatch | None = None,
        r_edit: dict[str, object] | None = None,
        e4_kind: str | None = None,
    ) -> CaseFile:
        lab = labels.get(variant, Labels())
        if e4_kind is not None:
            lab = lab.model_copy(update={"e4_kind": e4_kind})
        return CaseFile.model_validate({
            "id": f"{sc.id}:{variant}", "scenario_id": sc.id,
            "set": "R" if variant.startswith("R_") else "E", "variant": variant,
            "request": sc.request.model_dump(mode="json"),
            "package": {**e1, "cited": cited if cited is not None else e1["cited"]},
            "db_patch": patch.model_dump(mode="json") if patch else None,
            "r_edit": r_edit, "labels": lab.model_dump(mode="json", by_alias=True),
        })  # fmt: skip

    out = [case("E1"), case("E2", cited=sample_e2(sc, facts))]
    if sc.e3:
        moved = sc.e3.record_ids or sc.suspicious_record_ids
        out.append(case("E3", patch=DbPatch(ops=[MoveHost(record_ids=moved, host=sc.e3.host)])))
    if sc.e4:
        if sc.e4.kind == "partial_chain":
            remaining = [i for i in sc.e1.cited if i not in set(sc.e4.remove_record_ids)]
            if not remaining:
                raise BuildError("E4 partial chain removes every cited record")
            patch = DbPatch(ops=[Remove(record_ids=sc.e4.remove_record_ids)])
            out.append(case("E4", cited=remaining, patch=patch, e4_kind="partial_chain"))
        else:
            assert sc.e4.add is not None
            added = facts.max_record_id + 1  # the patcher assigns max + 1
            out.append(case("E4", cited=[*sc.e1.cited, added], patch=DbPatch(ops=[sc.e4.add]),
                            e4_kind="contradiction"))  # fmt: skip
    if sc.e5:
        out.append(case("E5", patch=DbPatch(ops=sc.e5.ops)))
    if sc.set_r:
        for variant, which in (("R_pos", "positive"), ("R_neg", "negative")):
            edit = sc.set_r.edit(which)  # type: ignore[arg-type]
            out.append(case(variant, r_edit=edit.model_dump(mode="json")))  # type: ignore[arg-type]
    return out
