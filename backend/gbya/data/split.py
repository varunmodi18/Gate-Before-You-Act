"""Seeded, tactic-stratified split of eligible windows into dev / test / e2e (plan §D.2, T1.6).

Method (deterministic for a given seed and input):

1. Units are de-duplication groups (T1.5); a group is never divided between splits.
2. Each group's stratum is the primary tactic of its smallest-id window (the first tactic of
   that window's first ATT&CK mapping).
3. Within each stratum the groups are shuffled with the seed. The strata are then interleaved
   round-robin (largest stratum first, ties by name), so every prefix of the resulting *seeded
   order* is close to the overall tactic mix.
4. Each split has a quota per tactic, proportional to the stratum sizes (largest remainder).
   Walking the seeded order, each group goes to the split with the largest unmet quota for its
   tactic (then the lowest fill ratio), among placements that keep an exact final fill possible.
   A group whose tactic quotas are all met is left ``unused`` when that keeps the fill possible.
5. The ``unused`` groups, in seeded order, form the replacement queue used when a scenario is
   excluded for evidence size (§D.7.1: prefer the same tactic, keep groups whole).

    python -m gbya.data.split    # make splits → data/splits.json (team review before commit)
"""

from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gbya.data.catalogue import TACTIC_NAMES

SPLIT_VERSION = 1
DEFAULT_SEED = 2026
TARGETS: dict[str, int] = {"dev": 10, "test": 40, "e2e": 12}  # plan §D.2 / FR-03
SPLIT_ORDER = ("dev", "test", "e2e")  # tie-break when fill ratios are equal


METHOD = (
    "Units are de-duplication groups (never divided). Stratum = primary tactic of the group's "
    "smallest-id window. Groups shuffled with the seed within each stratum, strata interleaved "
    "round-robin (largest first). Each split has a per-tactic quota proportional to stratum "
    "sizes; walking the order, each group goes to the split with the largest unmet quota for "
    "its tactic among placements that keep an exact final fill possible, otherwise unused. "
    "Unused windows in this order "
    "form the replacement queue."
)


class SplitError(ValueError):
    pass


@dataclass(frozen=True)
class WindowInfo:
    id: str
    dedup_group: str
    primary_tactic: str
    eligible: bool = True
    techniques: tuple[str, ...] = ()
    events: int | None = None
    primary_host: str | None = None
    primary_host_share: float | None = None


@dataclass
class SplitResult:
    seed: int
    targets: dict[str, int]
    assignment: dict[str, str]  # window id → dev / test / e2e / unused / ineligible
    seeded_order: list[str]  # group ids in seeded order
    replacement_order: list[str] = field(default_factory=list)  # unused window ids, in order

    def members(self, split: str) -> list[str]:
        return sorted(w for w, s in self.assignment.items() if s == split)


def _groups(windows: list[WindowInfo]) -> dict[str, list[WindowInfo]]:
    groups: dict[str, list[WindowInfo]] = defaultdict(list)
    for w in windows:
        groups[w.dedup_group].append(w)
    return {g: sorted(ws, key=lambda w: w.id) for g, ws in groups.items()}


def seeded_group_order(groups: dict[str, list[WindowInfo]], seed: int) -> list[str]:
    strata: dict[str, list[str]] = defaultdict(list)
    for gid, members in groups.items():
        strata[members[0].primary_tactic].append(gid)
    rng = random.Random(seed)
    for tactic in sorted(strata):  # sorted so the RNG is consumed in a fixed order
        strata[tactic].sort()
        rng.shuffle(strata[tactic])
    ordered_strata = sorted(strata, key=lambda t: (-len(strata[t]), t))
    order: list[str] = []
    depth = 0
    while len(order) < len(groups):
        for t in ordered_strata:
            if depth < len(strata[t]):
                order.append(strata[t][depth])
        depth += 1
    return order


def _quotas(strata: Counter[str], targets: dict[str, int]) -> dict[str, Counter[str]]:
    """Per split, windows per tactic proportional to the stratum sizes (largest remainder)."""
    total = sum(strata.values())
    quotas: dict[str, Counter[str]] = {}
    for name, target in targets.items():
        exact = {t: target * n / total for t, n in strata.items()}
        q = Counter({t: int(v) for t, v in exact.items()})
        short = target - sum(q.values())
        for t in sorted(exact, key=lambda t: (-(exact[t] - int(exact[t])), t))[:short]:
            q[t] += 1
        quotas[name] = q
    return quotas


def _reachable_remainders(sizes: list[int], caps: list[int]) -> list[set[tuple[int, ...]]]:
    """``reachable[i]`` = remaining capacities that groups ``i..`` can fill exactly.

    Each group goes to one split or is left unused. Computed backwards, so the forward walk can
    keep every placement consistent with an exact final fill.
    """
    reachable: list[set[tuple[int, ...]]] = [set() for _ in range(len(sizes) + 1)]
    reachable[len(sizes)] = {(0,) * len(caps)}
    for i in range(len(sizes) - 1, -1, -1):
        nxt = reachable[i + 1]
        cur = set(nxt)
        for rem in nxt:
            for k in range(len(caps)):
                grown = tuple(r + sizes[i] if j == k else r for j, r in enumerate(rem))
                if grown[k] <= caps[k]:
                    cur.add(grown)
        reachable[i] = cur
    return reachable


def assign_splits(
    windows: list[WindowInfo], seed: int = DEFAULT_SEED, targets: dict[str, int] | None = None
) -> SplitResult:
    targets = dict(targets or TARGETS)
    assignment = {w.id: "ineligible" for w in windows if not w.eligible}
    eligible = [w for w in windows if w.eligible]
    if len(eligible) < sum(targets.values()):
        raise SplitError(
            f"{len(eligible)} eligible windows < {sum(targets.values())} needed (plan Q-2)"
        )
    groups = _groups(eligible)
    order = seeded_group_order(groups, seed)
    names = [n for n in SPLIT_ORDER if n in targets]
    sizes = [len(groups[gid]) for gid in order]
    reachable = _reachable_remainders(sizes, [targets[n] for n in names])
    remaining = tuple(targets[n] for n in names)
    if remaining not in reachable[0]:
        raise SplitError(f"the de-duplication groups cannot fill {targets} exactly")
    stratum = {gid: groups[gid][0].primary_tactic for gid in order}
    quota = _quotas(Counter(stratum[g] for g in order for _ in groups[g]), targets)
    got: dict[str, Counter[str]] = {n: Counter() for n in names}
    filled = dict.fromkeys(names, 0)
    unused: list[str] = []
    for i, gid in enumerate(order):
        size, tactic = sizes[i], stratum[gid]
        options = []
        for k, name in enumerate(names):
            after = tuple(r - size if j == k else r for j, r in enumerate(remaining))
            if after[k] >= 0 and after in reachable[i + 1]:
                deficit = quota[name][tactic] - got[name][tactic]
                options.append((-deficit, filled[name] / targets[name], k, name, after))
        best = min(options) if options else None
        leave_out_ok = remaining in reachable[i + 1]
        if best is not None and (best[0] < 0 or not leave_out_ok):
            # largest unmet quota for this tactic, then least-filled split
            chosen, remaining = best[3], best[4]
            filled[chosen] += size
            got[chosen][tactic] += size
            for w in groups[gid]:
                assignment[w.id] = chosen
        else:  # every quota for this tactic is met (or nothing fits): leave it out
            unused.append(gid)
            for w in groups[gid]:
                assignment[w.id] = "unused"
    if remaining != (0,) * len(names):
        raise SplitError(f"could not fill the splits exactly: {filled} vs {targets}")
    replacement = [w.id for gid in unused for w in groups[gid]]
    return SplitResult(seed, targets, assignment, order, replacement)


def tactic_coverage(result: SplitResult, windows: list[WindowInfo]) -> dict[str, dict[str, int]]:
    by_id = {w.id: w for w in windows}
    cov: dict[str, Counter[str]] = defaultdict(Counter)
    for wid, split in result.assignment.items():
        cov[split][TACTIC_NAMES.get(by_id[wid].primary_tactic, by_id[wid].primary_tactic)] += 1
    return {s: dict(sorted(c.items())) for s, c in sorted(cov.items())}


def to_json(result: SplitResult, windows: list[WindowInfo], extra: dict[str, Any]) -> str:
    by_id = {w.id: w for w in windows}
    doc = {
        "version": SPLIT_VERSION,
        "seed": result.seed,
        "targets": result.targets,
        **extra,
        "method": METHOD,
        "splits": {s: result.members(s) for s in (*SPLIT_ORDER, "unused", "ineligible")},
        "replacement_order": result.replacement_order,
        "tactic_coverage": tactic_coverage(result, windows),
        "windows": {
            wid: {
                "split": result.assignment[wid],
                "dedup_group": by_id[wid].dedup_group,
                "primary_tactic": by_id[wid].primary_tactic,
                "techniques": list(by_id[wid].techniques),
                "events": by_id[wid].events,
                "primary_host": by_id[wid].primary_host,
                "primary_host_share": by_id[wid].primary_host_share,
            }
            for wid in sorted(result.assignment)
        },
    }
    return json.dumps(doc, indent=2) + "\n"


def main() -> None:
    import argparse

    from sqlalchemy import select

    from gbya.config import get_settings
    from gbya.data.connection import open_case_db
    from gbya.data.dedup import eligibility
    from gbya.data.fetch import OTRF_COMMIT
    from gbya.store.db import make_engine, make_sessionmaker, session_scope, upgrade
    from gbya.store.models import Window

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = ap.parse_args()
    settings = get_settings()
    upgrade()
    factory = make_sessionmaker(make_engine())
    infos: list[WindowInfo] = []
    with session_scope(factory) as s:
        rows = s.scalars(select(Window).order_by(Window.id)).all()
        if any(r.duckdb_path and not r.dedup_group for r in rows):
            raise SystemExit("run `make dedup` first")
        for r in rows:
            if not r.duckdb_path:
                continue  # not ingested (e.g. missing Host file)
            con = open_case_db(settings.resolve(Path(r.duckdb_path)))
            try:
                elig = eligibility(r.id, con)
            finally:
                con.close()
            infos.append(
                WindowInfo(
                    id=r.id,
                    dedup_group=str(r.dedup_group),
                    primary_tactic=(r.tactics or ["unknown"])[0],
                    eligible=elig.eligible,
                    techniques=tuple(r.techniques or []),
                    events=elig.events,
                    primary_host=elig.primary_host,
                    primary_host_share=elig.primary_host_share,
                )
            )
    result = assign_splits(infos, seed=args.seed)
    out = settings.resolve(settings.data_dir) / "splits.json"
    out.write_text(to_json(result, infos, {"otrf_commit": OTRF_COMMIT}))
    with session_scope(factory) as s:
        for wid, split in result.assignment.items():
            row = s.get(Window, wid)
            if row is not None:
                row.split = split if split in TARGETS or split == "unused" else None
    counts = Counter(result.assignment.values())
    print(f"wrote {out}: {dict(sorted(counts.items()))}")
    for split, cov in tactic_coverage(result, infos).items():
        print(f"  {split:8s} {cov}")


if __name__ == "__main__":
    main()
