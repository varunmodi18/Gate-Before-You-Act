"""Regenerated compute budget (plan T8.0, step 2).

    make budget                       # token audit, then this; writes docs/compute_budget.md
    uv run python scripts/budget.py --pilot-exp2 <run> --pilot-exp3 <run> [--apply]

Per experiment and call type (proposer, verifier, investigator): calls x mean input tokens
(``docs/token_audit.json``) and the output cap, a 10% retry allowance, hours at the T0.3 rates
(``docs/pilot/<profile>.json``: input and output tokens/s with four concurrent requests, counted
additively as in the proposal), then the 1.5x allowance; the total is compared with the 81-hour
planning figure and the ~112 hours available. Calls per episode come from a pilot run when given
(measured means), otherwise from the proposal (Exp 2: ~4 proposer calls; Exp 3: ~25 calls),
stated in the output. ``--apply`` writes the total into ``config/app.yaml`` as the budget
monitor's limit (``gpu_budget_hours``, T5.5). F1 (``experiments/fallbacks.yaml``) is shown when
the total exceeds the hours available.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
AUDIT = REPO / "docs" / "token_audit.json"
OUT = REPO / "docs" / "compute_budget.md"
PLANNING_H, AVAILABLE_H = 81.0, 112.0
RETRY, ALLOWANCE = 1.10, 1.5
TEST_SCENARIOS, CASES_PER_SCENARIO, E2E_WINDOWS = 40, 7, 12


def _yaml(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO / "experiments" / name).read_text())
    return data


def pilot_calls(run_id: int | None) -> dict[str, float] | None:
    """Mean model calls per episode of a pilot run: proposer, verifier, investigator."""
    if run_id is None:
        return None
    from sqlalchemy import select

    from gbya.config import get_settings
    from gbya.store import db
    from gbya.store.models import Episode, GateDecisionRow, Step

    s = get_settings()
    factory = db.make_sessionmaker(db.make_engine(s.resolve(s.app_db_path)))
    with factory() as ses:
        eps = list(ses.scalars(select(Episode).where(Episode.run_id == run_id,
                                                     Episode.status == "done")))  # fmt: skip
        if not eps:
            raise SystemExit(f"pilot run {run_id} has no finished episodes")
        ids = [e.id for e in eps]
        steps = list(ses.scalars(select(Step).where(Step.episode_id.in_(ids), Step.kind == "llm")))
        inv = sum(1 for st in steps if st.payload.get("phase") == "investigation")
        prop = len(steps) - inv
        ver = sum(1 for g in ses.scalars(select(GateDecisionRow).where(
            GateDecisionRow.episode_id.in_(ids))) if g.verifier)  # fmt: skip
    n = len(eps)
    return {"proposer": prop / n, "verifier": ver / n, "investigator": inv / n, "episodes": n}


def compute(audit: dict[str, Any], rates: dict[str, float], p2: dict[str, float] | None,
            p3: dict[str, float] | None) -> dict[str, Any]:  # fmt: skip
    from gbya.gate.config import load_configs

    configs = load_configs()
    e1, e2, e3 = _yaml("exp1.yaml"), _yaml("exp2.yaml"), _yaml("exp3.yaml")
    if e2.get("fallback") == "F1":
        f1 = _yaml("fallbacks.yaml")["F1"]
        systems2 = dict(f1["runs"])
    else:
        systems2 = dict(e2["systems"])
    cases = TEST_SCENARIOS * CASES_PER_SCENARIO
    pin = audit["proposer"]["all"]["mean"] or 0.0
    vin = audit["verifier"]["all"]["mean"] or 0.0
    iin = audit["investigator"]["all"]["mean"] or 0.0
    pout, vout = audit["proposer"]["output_cap"], audit["verifier"]["output_cap"]
    iout = max(audit["investigator"]["output_caps"].values())
    c4 = {sid for sid, c in configs.items() if "C4" in c.checks}
    eps2 = {sid: cases * runs for sid, runs in systems2.items()}
    eps3 = {sid: E2E_WINDOWS * runs for sid, runs in e3["systems"].items()}
    prop2 = p2["proposer"] if p2 else 4.0
    ver2 = p2["verifier"] if p2 else 1.0
    inv3 = p3["investigator"] if p3 else 21.0  # proposal: ~25 calls in all per Exp 3 episode
    prop3 = p3["proposer"] if p3 else 4.0
    calls1 = cases * len(e1["verifier_variants"]) * e1["verifier_runs"]
    lines = [
        ("Exp 1", "verifier", calls1, vin, vout),
        ("Exp 2", "proposer", sum(eps2.values()) * prop2, pin, pout),
        ("Exp 2", "verifier", sum(n for s, n in eps2.items() if s in c4) * ver2, vin, vout),
        ("Exp 3", "investigator", sum(eps3.values()) * inv3, iin, iout),
        ("Exp 3", "proposer", sum(eps3.values()) * prop3, pin, pout),
        ("Exp 3", "verifier", sum(n for s, n in eps3.items() if s in c4) * ver2, vin, vout),
    ]  # fmt: skip
    rows = []
    for exp, kind, calls, tin, tout in lines:
        tok_in, tok_out = calls * tin * RETRY, calls * tout * RETRY
        hours = (tok_in / rates["in"] + tok_out / rates["out"]) / 3600
        rows.append({"experiment": exp, "call": kind, "calls": round(calls),
                     "input_tokens": round(tok_in), "output_tokens": round(tok_out),
                     "hours": round(hours, 2)})  # fmt: skip
    raw = sum(r["hours"] for r in rows)
    total = raw * ALLOWANCE
    return {"rows": rows, "hours_before_allowance": round(raw, 1),
            "hours_with_allowance": round(total, 1), "planning_hours": PLANNING_H,
            "available_hours": AVAILABLE_H, "exceeds_available": total > AVAILABLE_H,
            "fallback": e2.get("fallback"), "rates": rates,
            "assumptions": {"exp2_proposer_calls_per_episode": prop2,
                            "verifier_calls_per_episode": ver2,
                            "exp3_investigator_calls_per_episode": inv3,
                            "exp3_proposer_calls_per_episode": prop3,
                            "source": {"exp2": "pilot" if p2 else "proposal",
                                       "exp3": "pilot" if p3 else "proposal"},
                            "outputs": "at the output caps (upper bound)"}}  # fmt: skip


def markdown(audit: dict[str, Any], b: dict[str, Any]) -> str:
    lines = ["# Compute budget (regenerated, T8.0)", "",
             f"Generated by `scripts/budget.py` from `docs/token_audit.json` (tokenizer "
             f"`{audit['tokenizer']}`, {audit['cases']} dev cases, "
             f"{audit['investigated_windows']} windows investigated) and the T0.3 rates "
             f"({b['rates']['in']} input / {b['rates']['out']} output tokens/s). Replaces the "
             "proposal's planning estimate (81 h).", "",
             "| Experiment | Call | Calls | Input tokens | Output tokens | Hours |",
             "|---|---|---|---|---|---|"]  # fmt: skip
    for r in b["rows"]:
        lines.append(
            f"| {r['experiment']} | {r['call']} | {r['calls']:,} | "
            f"{r['input_tokens']:,} | {r['output_tokens']:,} | {r['hours']} |"
        )
    lines += ["", f"**{b['hours_before_allowance']} h** before the 1.5x allowance, **"
              f"{b['hours_with_allowance']} h** with it (planning figure {b['planning_hours']} h; "
              f"about {b['available_hours']} h available). "
              + ("**Exceeds the hours available: apply fallback F1 and record it before T9.1.**"
                 if b["exceeds_available"] else "Within the hours available."), "",
              "Assumptions: " + json.dumps(b["assumptions"]), "",
              "Per-request checks of the audit: " + json.dumps(audit["checks"]), ""]  # fmt: skip
    return "\n".join(lines)


def apply_limit(hours: float, path: Path = REPO / "config" / "app.yaml") -> None:
    text = path.read_text()
    line = f"gpu_budget_hours: {hours}          # T8.0 regenerated budget (scripts/budget.py)"
    if re.search(r"^gpu_budget_hours:", text, re.M):
        text = re.sub(r"^gpu_budget_hours:.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit", type=Path, default=AUDIT)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--pilot-exp2", type=int)
    ap.add_argument("--pilot-exp3", type=int)
    ap.add_argument("--profile", default="vllm-awq")
    ap.add_argument("--apply", action="store_true", help="set gpu_budget_hours in config/app.yaml")
    args = ap.parse_args(argv)
    audit = json.loads(args.audit.read_text())
    pilot = json.loads((REPO / "docs" / "pilot" / f"{args.profile}.json").read_text())
    ep = pilot["episodes_20"]
    rates = {"in": float(ep["input_tok_per_s"]), "out": float(ep["output_tok_per_s"])}
    b = compute(audit, rates, pilot_calls(args.pilot_exp2), pilot_calls(args.pilot_exp3))
    args.out.write_text(markdown(audit, b))
    if args.apply:
        apply_limit(b["hours_with_allowance"])
    print(json.dumps({k: b[k] for k in ("hours_before_allowance", "hours_with_allowance",
                                         "exceeds_available")}), "->", args.out)  # fmt: skip


if __name__ == "__main__":
    main()
