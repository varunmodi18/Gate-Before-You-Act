"""Preliminary sensitivity from a pilot (plan T7.3, §D.12).

    uv run python scripts/sensitivity.py --exp1 RUN_ID --exp2 RUN_ID

Reads an early partial Exp 1 and Exp 2 run on **dev** scenarios, estimates the within-scenario
correlation and discordance per hypothesis, and prints the expected 95% half-widths at the full
design size (H1 160 cases, H1-C4 80, H2 120), next to the proposal's assumptions (rho 0.2,
discordance 0.30). The numbers go into STATUS.md as "preliminary sensitivity", never as power.
"""

from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> None:
    from gbya.analysis.sensitivity import pilot_report
    from gbya.api.routers.analysis import episode_rows, provenance
    from gbya.config import get_settings
    from gbya.scoring.results import exp1_rows
    from gbya.store.db import make_engine, make_sessionmaker
    from gbya.store.models import Run

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp1", type=int)
    ap.add_argument("--exp2", type=int)
    args = ap.parse_args(argv)
    settings = get_settings()
    factory = make_sessionmaker(make_engine(settings.resolve(settings.app_db_path)))
    with factory() as s:
        runs = {k: s.get(Run, v) for k, v in (("exp1", args.exp1), ("exp2", args.exp2)) if v}
        rows = exp1_rows(s, runs["exp1"].id) if runs.get("exp1") else []
        eps = episode_rows(s, runs["exp2"].id) if runs.get("exp2") else []
        report = pilot_report(rows, eps)
        report["provenance"] = {k: provenance(r) for k, r in runs.items() if r is not None}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
