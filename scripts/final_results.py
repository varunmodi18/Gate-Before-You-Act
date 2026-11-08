"""Final results export (plan T9.4): docs/final/results.json, tables as CSV, figures as SVG.

    uv run python scripts/final_results.py --exp1 1 --exp2 2 --exp3 3     # API on 127.0.0.1:8000
    node scripts/svg_to_png.mjs docs/final/figures                         # PNG next to each SVG

Every headline number comes from the research endpoints, which refuse replay, non-research,
unfinished, unfrozen or pending data: ``POST /analysis/hypotheses`` (H1, H1-C4, H2 with verdicts;
cluster bootstrap over scenarios, B = 10,000, seed 2026, percentile 95% CIs), ``POST
/analysis/compare`` (capability ablations; ``matched_run: 1`` whenever run counts differ),
``GET /runs/{id}/results|retrieval|verifier-eval|outcomes|taxonomy``. Breakdowns by variant and
by objective apply the same ``gbya.analysis`` functions (``admitted_on_reject``,
``deferral_on_justified``, ``paired``) to subsets of the same rows; they carry no verdict and are
marked exploratory. The error taxonomy's six tags are applied by people on the Traces page; this
export reports the three tags the code **suggests** (``scoring/taxonomy.py``) and ten example
traces chosen by a fixed rule (listed in the output). Proportions internally; ``*_pp`` fields are
display values (§L.4 item 12). Read-only on app.db.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "final"
API = "http://127.0.0.1:8000/api/v1"
B, SEED = 10_000, 2026
OBJECTIVES = ("contain_host", "stop_process", "disable_account")
EXP2_ORDER = ("G0", "G1", "G2", "A1", "G3", "A5", "A7", "A8")
EXP1_ORDER = ("G0", "G1", "G2", "A1", "A2", "G3", "A3", "A4", "A6")
OUTCOMES = ("safe_completion", "justified_escalation", "unnecessary_deferral", "refusal",
            "unsafe_execution")  # fmt: skip


def pp(x: float | None) -> float | None:
    return None if x is None else round(100 * x, 1)


def get(path: str) -> Any:
    with urllib.request.urlopen(API + path, timeout=600) as r:
        return json.loads(r.read())


def post(path: str, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})  # fmt: skip
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:  # a refusal is reported, never hidden
        return {"refused": json.loads(exc.read() or b"{}"), "status": exc.code}


def write_csv(name: str, rows: list[dict[str, Any]], note: str) -> None:
    path = OUT / name
    with path.open("w", newline="") as f:
        f.write(f"# {note}\n")
        if rows:
            fields = list(dict.fromkeys(k for r in rows for k in r))
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)


def interval_row(iv: Any) -> dict[str, Any]:
    return {"estimate": iv.estimate, "ci_low": iv.ci_low, "ci_high": iv.ci_high,
            "estimate_pp": pp(iv.estimate), "ci_low_pp": pp(iv.ci_low),
            "ci_high_pp": pp(iv.ci_high), "n_scenarios": iv.n_scenarios,
            "n_cases": iv.n_cases}  # fmt: skip


# ---- runs ----------------------------------------------------------------------------------------


def run_rows(run_ids: dict[str, int]) -> list[dict[str, Any]]:
    out = []
    for exp, rid in run_ids.items():
        r = get(f"/runs/{rid}")
        cfg = r.get("config") or {}
        systems = cfg.get("systems") or {
            s: 1 for s in (cfg.get("spec") or {}).get("code_only", [])
        } | {s: (cfg.get("spec") or {}).get("verifier_runs") for s in
             (cfg.get("spec") or {}).get("composed", [])}  # fmt: skip
        start = datetime.fromisoformat(r["started_at"])
        end = datetime.fromisoformat(r["finished_at"])
        wall_h = (end - start).total_seconds() / 3600
        p = r["progress"]
        out.append({"run_id": rid, "experiment": exp, "purpose": r["purpose"],
                    "status": r["status"], "systems": json.dumps(systems, sort_keys=False),
                    "n_cases": len(cfg.get("case_ids", [])), "n_units": p["total"],
                    "completed": p["done"], "errors": p["errors"],
                    "error_rate": p["errors"] / p["total"], "started_utc": r["started_at"],
                    "finished_utc": r["finished_at"], "wall_h": round(wall_h, 3),
                    "gpu_h": round(wall_h, 3), "git_sha": r["git_sha"],
                    "config_hash": r["config_hash"], "case_set_hash": r["case_set_hash"],
                    "model_id": r["model_id"], "model_file_sha256": r["model_file_sha256"],
                    "backend": r["backend"]})  # fmt: skip
    return out


# ---- breakdowns (exploratory, same functions on subsets) -----------------------------------------


def breakdowns(
    rows1: list[Any], eps: list[Any], objective_of: dict[str, str]
) -> list[dict[str, Any]]:
    from gbya.analysis import hypotheses as hy
    from gbya.analysis.bootstrap import paired

    def sc(rs: Iterable[Any]) -> dict[str, str]:
        return {r.case_id: r.scenario_id for r in rs}

    out: list[dict[str, Any]] = []

    def add(hyp: str, by: str, value: str, a: str, b: str, ia: dict[str, float],
            ib: dict[str, float], scen: dict[str, str]) -> None:  # fmt: skip
        row = {"hypothesis": hyp, "breakdown": by, "value": value, "a": a, "b": b,
               "rate_a": None, "rate_b": None, "exploratory": True}  # fmt: skip
        common = sorted(set(ia) & set(ib))
        if not common:
            out.append(row | {"note": "no cases in this subset"})
            return
        row["rate_a"] = sum(ia[c] for c in common) / len(common)
        row["rate_b"] = sum(ib[c] for c in common) / len(common)
        iv = paired({c: ia[c] for c in common}, {c: ib[c] for c in common}, scen, B=B, seed=SEED)
        out.append(row | interval_row(iv) | {"rate_a_pp": pp(row["rate_a"]),
                                             "rate_b_pp": pp(row["rate_b"])})  # fmt: skip

    s1, s2 = sc(rows1), sc(eps)
    for name, a, b, variants in (("H1", "G1", "G3", hy.H1_VARIANTS),
                                 ("H1_C4", "A1", "G3", hy.H1_C4_VARIANTS)):  # fmt: skip
        for v in variants:
            add(
                name,
                "variant",
                v,
                a,
                b,
                hy.admitted_on_reject(rows1, a, {v}),
                hy.admitted_on_reject(rows1, b, {v}),
                s1,
            )
        for o in OBJECTIVES:
            sub = [r for r in rows1 if objective_of.get(r.case_id) == o]
            add(
                name,
                "objective",
                o,
                a,
                b,
                hy.admitted_on_reject(sub, a, variants),
                hy.admitted_on_reject(sub, b, variants),
                s1,
            )
    for v in ("E1", "E2", "E3", "R_pos"):
        sub = [e for e in eps if e.variant == v]
        add(
            "H2",
            "variant",
            v,
            "G3",
            "G1",
            hy.deferral_on_justified(sub, "G3"),
            hy.deferral_on_justified(sub, "G1"),
            s2,
        )
    for o in OBJECTIVES:
        sub = [e for e in eps if objective_of.get(e.case_id) == o]
        add(
            "H2",
            "objective",
            o,
            "G3",
            "G1",
            hy.deferral_on_justified(sub, "G3"),
            hy.deferral_on_justified(sub, "G1"),
            s2,
        )
    return out


def exp1_by_variant_objective(
    rows1: list[Any], objective_of: dict[str, str]
) -> list[dict[str, Any]]:
    """Wrongly-admitted (reject-labelled cases) and wrongly-blocked (admit-labelled) per system,
    variant and objective: decisions pooled over runs (descriptive)."""
    acc: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for r in rows1:
        for key in ((r.system, r.variant, objective_of.get(r.case_id, "?")),
                    (r.system, r.variant, "all"),
                    (r.system, "all", objective_of.get(r.case_id, "?")),
                    (r.system, "all", "all")):  # fmt: skip
            a = acc[key]
            if r.label == "reject" and r.variant in ("E2", "E3", "E4", "E5"):
                a[0] += r.verdict == "admitted"
                a[1] += 1
            elif r.label == "admit":
                a[2] += r.verdict != "admitted"
                a[3] += 1
    out = []
    for (s, v, o), (wa, n_rej, wb, n_adm) in sorted(acc.items()):
        out.append({"system": s, "variant": v, "objective": o,
                    "wrongly_admitted": wa / n_rej if n_rej else None,
                    "wrongly_admitted_pp": pp(wa / n_rej) if n_rej else None,
                    "n_reject_decisions": n_rej,
                    "wrongly_blocked": wb / n_adm if n_adm else None,
                    "wrongly_blocked_pp": pp(wb / n_adm) if n_adm else None,
                    "n_admit_decisions": n_adm})  # fmt: skip
    return out


def episode_outcomes(eps: list[Any], objective_of: dict[str, str], key: Callable[[Any], str],
                     by: str) -> list[dict[str, Any]]:  # fmt: skip
    from gbya.scoring.metrics import SUCCESS

    acc: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for e in eps:
        for k in (key(e), "all"):
            acc[(e.system, k)][e.outcome or "unscored"] += 1
    out = []
    for (s, k), c in sorted(acc.items()):
        n = sum(c.values())
        row: dict[str, Any] = {"system": s, by: k, "episodes": n}
        for o in OUTCOMES:
            row[o] = c[o] / n
            row[f"{o}_pp"] = pp(c[o] / n)
        row["task_success"] = sum(c[o] for o in SUCCESS) / n
        row["task_success_pp"] = pp(row["task_success"])
        out.append(row)
    return out


# ---- H2 sensitivity to the run adjudication (exploratory) ---------------------------------------


def h2_adjudication_sensitivity(session: Any, run_id: int, eps: list[Any]) -> dict[str, Any]:
    """H2 recomputed in memory with every adjudicated escalation counted as qualifying (call
    decisions unchanged): how far the run adjudication of unlisted escalations moves H2. Uses the
    pure classifier (``events_from_steps`` + ``classify``); app.db is not written. Not the
    registered analysis."""
    from dataclasses import replace

    from sqlalchemy import select

    from gbya.analysis import hypotheses as hy
    from gbya.cases.models import Labels
    from gbya.scoring.outcomes import (
        EscalationAdjudication,
        classify,
        events_from_steps,
        load_adjudications,
    )
    from gbya.store.models import Case, Episode, Step

    changed: Counter[tuple[str, str, str]] = Counter()
    out = []
    by_id = {
        e.id: e
        for e in session.scalars(
            select(Episode).where(Episode.run_id == run_id, Episode.status == "done")
        )
    }
    lite_of = {}
    for e in session.scalars(
        select(Episode).where(Episode.run_id == run_id, Episode.status == "done")
    ):
        lite_of[(e.case_id, e.system, e.run_idx)] = e.id
    for lite in eps:
        if lite.system not in ("G1", "G3"):
            out.append(lite)
            continue
        ep = by_id[lite_of[(lite.case_id, lite.system, lite.run_idx)]]
        adj = load_adjudications(session, str(ep.case_id))
        flipped = {
            k: (EscalationAdjudication(True) if isinstance(v, EscalationAdjudication) else v)
            for k, v in adj.items()
        }
        case = session.get(Case, ep.case_id)
        labels = Labels.model_validate(case.labels or {})
        steps = session.scalars(select(Step).where(Step.episode_id == ep.id).order_by(Step.idx))
        events = events_from_steps(((s.kind, s.payload) for s in steps), ep.terminal_state)
        new = classify(events, labels, flipped).outcome
        if new != lite.outcome:
            changed[(lite.system, str(lite.outcome), new)] += 1
        out.append(replace(lite, outcome=new))
    res = hy.h2(out, B=B, seed=SEED).to_json()
    return {
        "exploratory": True,
        "what": "H2 with every run-adjudicated escalation counted as qualifying (calls unchanged)",
        "H2": res,
        "changed_outcomes": [
            {"system": s, "from": a, "to": b, "episodes": n}
            for (s, a, b), n in sorted(changed.items())
        ],
    }


# ---- taxonomy suggestions and example traces -----------------------------------------------------


def taxonomy(session: Any, run_ids: list[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from sqlalchemy import select

    from gbya.scoring.taxonomy import ERROR_TAGS, episode_suggestions
    from gbya.store.models import Episode

    counts: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    tagged: list[tuple[Any, list[str]]] = []
    for rid in run_ids:
        for ep in session.scalars(select(Episode).where(Episode.run_id == rid,
                                                        Episode.status == "done")
                                  .order_by(Episode.id)):  # fmt: skip
            tags = episode_suggestions(session, ep.id)
            c = counts[(rid, ep.system)]
            c["episodes"] += 1
            for t in tags:
                c[t] += 1
            if tags or ep.outcome == "unsafe_execution":
                tagged.append((ep, tags))
    table = [{"run_id": rid, "system": s, "episodes": c["episodes"],
              **{f"suggested_{t}": c[t] for t in ERROR_TAGS}}
             for (rid, s), c in sorted(counts.items())]  # fmt: skip
    # Ten example traces by a fixed rule: categories in this order with these quotas, within a
    # category systems round-robin in EXP2_ORDER, lowest episode id first; shortfall filled from
    # the next category.
    cats: list[tuple[str, int, Callable[[Any, list[str]], bool]]] = [
        ("wrong_target (suggested)", 3, lambda e, t: "wrong_target" in t),
        ("injection_followed (suggested)", 2, lambda e, t: "injection_followed" in t),
        ("over_deferral (suggested)", 3, lambda e, t: "over_deferral" in t),
        ("unsafe_execution outcome", 2, lambda e, t: e.outcome == "unsafe_execution"
         and "wrong_target" not in t),
    ]  # fmt: skip
    chosen: list[dict[str, Any]] = []
    seen: set[int] = set()
    carry = 0
    for name, quota, pred in cats:
        quota += carry
        pool: dict[str, list[tuple[Any, list[str]]]] = defaultdict(list)
        for e, t in tagged:
            if pred(e, t) and e.id not in seen:
                pool[e.system].append((e, t))
        picked = 0
        while picked < quota and any(pool.values()):
            for s in EXP2_ORDER:
                if picked < quota and pool.get(s):
                    e, t = pool[s].pop(0)
                    seen.add(e.id)
                    chosen.append({"episode_id": e.id, "run_id": e.run_id, "case_id": e.case_id,
                                   "system": e.system, "run_idx": e.run_idx, "category": name,
                                   "outcome": e.outcome, "suggested_tags": ";".join(t),
                                   "trace_url": f"/traces/{e.id}"})  # fmt: skip
                    picked += 1
        carry = quota - picked
    return table, chosen


# ---- figures (SVG; PNG via scripts/svg_to_png.mjs) ----------------------------------------------

INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df", "#fcfcfb"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")  # reference palette, validated
SEQ = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
FONT = "font-family='Helvetica, Arial, sans-serif'"


def svg_doc(w: int, h: int, body: str, title: str, desc: str) -> str:
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' "
            f"viewBox='0 0 {w} {h}' role='img'><title>{escape(title)}</title>"
            f"<desc>{escape(desc)}</desc><rect width='{w}' height='{h}' fill='{SURFACE}'/>"
            f"{body}</svg>")  # fmt: skip


def text(x: float, y: float, s: str, size: int = 13, fill: str = INK, anchor: str = "start",
         weight: str = "normal") -> str:  # fmt: skip
    return (
        f"<text x='{x:.1f}' y='{y:.1f}' {FONT} font-size='{size}' fill='{fill}' "
        f"text-anchor='{anchor}' font-weight='{weight}'>{escape(s)}</text>"
    )


def forest(h: dict[str, Any]) -> str:
    items = [
        ("H1  wrongly admitted E2\u2013E5, G1 \u2212 G3", h["H1"], h["H1"].get("target"), "target"),
        (
            "H1-C4  wrongly admitted E4\u2013E5, A1 \u2212 G3",
            h["H1_C4"],
            h["H1_C4"].get("target"),
            "target",
        ),
        ("H2  unnecessary deferral, G3 \u2212 G1", h["H2"], h["H2"].get("margin"), "margin"),
    ]
    w, left, right, top, row = 860, 330, 40, 70, 64
    hgt = top + row * len(items) + 60
    lo, hi = -0.1, 0.6
    sx = lambda v: left + (v - lo) / (hi - lo) * (w - left - right)  # noqa: E731
    body = text(
        20,
        30,
        "Hypotheses: difference of proportions with 95% cluster-bootstrap CI",
        16,
        weight="bold",
    )
    body += text(
        20,
        50,
        "Points: estimate; bars: 95% CI (B = 10,000, seed 2026, scenarios "
        "resampled); ticks: pre-registered target or margin",
        12,
        INK2,
    )
    for t in (-0.1, 0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        x = sx(t)
        body += f"<line x1='{x:.1f}' y1='{top - 10}' x2='{x:.1f}' y2='{hgt - 50}' stroke='{GRID}'/>"
        body += text(x, hgt - 32, f"{t:+.1f}" if t else "0", 11, MUTED, "middle")
    body += (
        f"<line x1='{sx(0):.1f}' y1='{top - 10}' x2='{sx(0):.1f}' y2='{hgt - 50}' "
        f"stroke='{MUTED}'/>"
    )
    body += text((left + w - right) / 2, hgt - 12, "difference (proportion)", 12, INK2, "middle")
    for i, (label, r, thr, kind) in enumerate(items):
        y = top + row * i + 20
        body += text(20, y + 4, label, 13)
        verdict = (f"target reached: {r['target_reached']}; statistically supported: "
                   f"{r['statistically_supported']}" if kind == "target"
                   else f"decision: {r['decision']}")  # fmt: skip
        body += text(20, y + 22, verdict, 11, INK2)
        body += (
            f"<line x1='{sx(r['ci_low']):.1f}' y1='{y}' x2='{sx(r['ci_high']):.1f}' "
            f"y2='{y}' stroke='{SERIES[0]}' stroke-width='2' stroke-linecap='round'/>"
        )
        body += (
            f"<circle cx='{sx(r['estimate']):.1f}' cy='{y}' r='5' fill='{SERIES[0]}' "
            f"stroke='{SURFACE}' stroke-width='2'/>"
        )
        tx = sx(thr)
        body += (
            f"<line x1='{tx:.1f}' y1='{y - 12}' x2='{tx:.1f}' y2='{y + 12}' "
            f"stroke='{INK}' stroke-width='2'/>"
        )
        body += text(tx, y - 16, f"{kind} {thr:.2f}", 10, INK2, "middle")
        body += text(
            sx(r["ci_high"]) + 8,
            y + 4,
            f"{r['estimate']:.3f} [{r['ci_low']:.3f}, {r['ci_high']:.3f}]",
            11,
            INK2,
        )
    return svg_doc(
        w,
        hgt,
        body,
        "Hypothesis estimates",
        "; ".join(
            f"{lbl}: {r['estimate']:.3f} [{r['ci_low']:.3f}, {r['ci_high']:.3f}]"
            for lbl, r, _, _ in items
        ),
    )


def outcome_bars(rows: list[dict[str, Any]], title: str, order: Iterable[str], by: str) -> str:
    data = {r["system"]: r for r in rows if r[by] == "all"}
    systems = [s for s in order if s in data]
    w, left, right, top, bar, gap = 900, 70, 30, 96, 26, 12
    hgt = top + (bar + gap) * len(systems) + 50
    sx = lambda v: left + v * (w - left - right)  # noqa: E731
    body = text(20, 30, title, 16, weight="bold")
    lx = 20
    for i, o in enumerate(OUTCOMES):  # legend
        body += f"<rect x='{lx}' y='50' width='12' height='12' rx='2' fill='{SERIES[i]}'/>"
        label = o.replace("_", " ")
        body += text(lx + 18, 60, label, 12, INK2)
        lx += 30 + 7.2 * len(label)
    body += text(20, 82, "Share of episodes per outcome (all runs pooled); labels in %", 11, MUTED)
    for j, s in enumerate(systems):
        y = top + j * (bar + gap)
        body += text(left - 10, y + bar / 2 + 4, f"{s}", 13, INK, "end")
        x = 0.0
        for i, o in enumerate(OUTCOMES):
            v = data[s][o]
            if v <= 0:
                continue
            x0, x1 = sx(x), sx(x + v)
            body += (
                f"<rect x='{x0 + 1:.1f}' y='{y}' width='{max(x1 - x0 - 2, 0.5):.1f}' "
                f"height='{bar}' rx='3' fill='{SERIES[i]}'/>"
            )
            if x1 - x0 > 34:
                body += text((x0 + x1) / 2, y + bar / 2 + 4, f"{100 * v:.0f}", 11, INK, "middle")
            x += v
        body += text(w - right + 4, y + bar / 2 + 4, f"n={data[s]['episodes']}", 10, MUTED)
    return svg_doc(
        w + 40,
        hgt,
        body,
        title,
        "; ".join(
            f"{s}: " + ", ".join(f"{o} {100 * data[s][o]:.1f}%" for o in OUTCOMES) for s in systems
        ),
    )


def heatmap(rows: list[dict[str, Any]]) -> str:
    variants = ("E2", "E3", "E4", "E5", "all")
    cell = {(r["system"], r["variant"]): r for r in rows if r["objective"] == "all"}
    systems = [s for s in EXP1_ORDER if (s, "all") in cell]
    w, left, top, cw, ch = 640, 70, 90, 100, 30
    hgt = top + ch * len(systems) + 40
    body = text(
        20,
        30,
        "Exp 1: wrongly-admitted rate on reject-labelled cases (E2\u2013E5)",
        16,
        weight="bold",
    )
    body += text(
        20,
        52,
        "Gate decisions pooled over the three verifier runs; values in %; darker = more admitted",
        11,
        INK2,
    )
    for i, v in enumerate(variants):
        body += text(left + i * cw + cw / 2, top - 10, v, 12, INK2, "middle")
    for j, s in enumerate(systems):
        y = top + j * ch
        body += text(left - 10, y + ch / 2 + 4, s, 13, INK, "end")
        for i, v in enumerate(variants):
            r = cell.get((s, v))
            val = r["wrongly_admitted"] if r else None
            if val is None:
                continue
            k = min(int(val * (len(SEQ) - 1) + 0.5), len(SEQ) - 1)
            fill = SEQ[k]
            body += (
                f"<rect x='{left + i * cw + 1}' y='{y + 1}' width='{cw - 2}' "
                f"height='{ch - 2}' rx='3' fill='{fill}'/>"
            )
            body += text(
                left + i * cw + cw / 2,
                y + ch / 2 + 4,
                f"{100 * val:.1f}",
                12,
                "#ffffff" if k >= 4 else INK,
                "middle",
            )
    return svg_doc(
        w,
        hgt,
        body,
        "Exp 1 wrongly admitted by variant",
        "; ".join(
            f"{s} {v}: {100 * cell[(s, v)]['wrongly_admitted']:.1f}%"
            for s in systems
            for v in variants
            if (s, v) in cell and cell[(s, v)]["wrongly_admitted"] is not None
        ),
    )


def proposed_admitted(rows: list[dict[str, Any]]) -> str:
    by = {r["system"]: r for r in rows if r["experiment"] == 2 and r["unsafe_proposed"]}
    rows = [by[s] for s in EXP2_ORDER if s in by]
    w, left, top, bar = 860, 70, 96, 14
    maxv = max(r["unsafe_proposed"] for r in rows)
    sx = lambda v: left + v / maxv * (w - left - 140)  # noqa: E731
    hgt = top + len(rows) * (2 * bar + 18) + 30
    body = text(20, 30, "Exp 2: unsafe calls proposed vs admitted by the gate", 16, weight="bold")
    for i, (lab, col) in enumerate(
        (("unsafe calls proposed", SERIES[0]), ("of which admitted (executed)", SERIES[1]))
    ):
        body += f"<rect x='{20 + i * 220}' y='50' width='12' height='12' rx='2' fill='{col}'/>"
        body += text(38 + i * 220, 60, lab, 12, INK2)
    body += text(
        20,
        82,
        "Prohibited or unapproved state-changing calls, all runs pooled (counts "
        "depend on the episodes: G0, G1, G3 651; others 217); share admitted in brackets",
        11,
        MUTED,
    )
    for j, r in enumerate(rows):
        y = top + j * (2 * bar + 18)
        body += text(left - 10, y + bar + 4, r["system"], 13, INK, "end")
        body += text(left - 10, y + 2 * bar + 4, f"n={r['episodes']}", 9, MUTED, "end")
        for k, (v, col) in enumerate(
            ((r["unsafe_proposed"], SERIES[0]), (r["unsafe_admitted"], SERIES[1]))
        ):
            yy = y + k * (bar + 2)
            body += (
                f"<rect x='{left}' y='{yy}' width='{max(sx(v) - left, 1):.1f}' "
                f"height='{bar}' rx='3' fill='{col}'/>"
            )
            label = f"{v}" + (f"  ({100 * r['unsafe_admitted_share']:.1f}%)" if k else "")
            body += text(sx(v) + 6, yy + bar - 2, label, 11, INK2)
    return svg_doc(
        w,
        hgt,
        body,
        "Unsafe proposed vs admitted",
        "; ".join(
            f"{r['system']}: proposed {r['unsafe_proposed']}, admitted {r['unsafe_admitted']}"
            for r in rows
        ),
    )


# ---- main ----------------------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp1", type=int, required=True)
    ap.add_argument("--exp2", type=int, required=True)
    ap.add_argument("--exp3", type=int, required=True)
    args = ap.parse_args()
    from gbya.api.routers.analysis import episode_rows
    from gbya.config import get_settings
    from gbya.scoring.results import exp1_rows
    from gbya.store.db import make_engine, make_sessionmaker
    from gbya.store.models import Case

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "figures").mkdir(exist_ok=True)
    ids = {"exp1": args.exp1, "exp2": args.exp2, "exp3": args.exp3}
    boot = {"B": B, "seed": SEED}

    hyp = post("/analysis/hypotheses", {"run_ids": {"exp1": args.exp1, "exp2": args.exp2}, **boot})
    if "refused" in hyp:
        raise SystemExit(f"hypotheses refused: {hyp}")
    res = {k: get(f"/runs/{v}/results") for k, v in ids.items()}
    outcomes = {k: get(f"/runs/{ids[k]}/outcomes") for k in ("exp2", "exp3")}
    retrieval = get(f"/runs/{args.exp1}/retrieval")
    veval = get(f"/runs/{args.exp1}/verifier-eval")
    human_tax = get(f"/runs/{args.exp2}/taxonomy")

    def cmp(run: int, a: str, b: str, metric: str, matched: int | None = None,
            variants: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
        body: dict[str, Any] = {"run_id": run, "a": a, "b": b, "metric": metric, **boot}
        if matched:
            body["matched_run"] = matched
        if variants:
            body["variants"] = variants
        return {
            "request": {k: v for k, v in body.items() if k not in ("B", "seed")},
            "result": post("/analysis/compare", body),
        }

    e2_5 = ["E2", "E3", "E4", "E5"]
    rag = [
        cmp(args.exp1, a, "G3", m, variants=e2_5 if m == "wrongly_admitted" else None)
        for a in ("A4", "A6")
        for m in ("wrongly_admitted", "wrongly_blocked")
    ]
    rag += [cmp(args.exp1, "A6", "A4", "wrongly_admitted", variants=e2_5)]
    metrics2 = ["task_success", "safe_completion", "justified_escalation",
                "unnecessary_deferral", "refusal", "unsafe_execution"]  # fmt: skip
    recovery = [
        cmp(args.exp2, a, b, m, matched=1)
        for a, b in (("A5", "G3"), ("A7", "G3"), ("A5", "A7"))
        for m in metrics2
    ]
    mcp = [cmp(args.exp2, "A8", "G3", m, matched=1) for m in metrics2]

    s = get_settings()
    factory = make_sessionmaker(make_engine(s.resolve(s.app_db_path)))
    with factory() as session:
        objective_of = {c.id: (c.request or {}).get("objective", "?")
                        for c in session.query(Case)}  # fmt: skip
        rows1 = exp1_rows(session, args.exp1)
        eps2 = episode_rows(session, args.exp2)
        eps3 = episode_rows(session, args.exp3)
        brk = breakdowns(rows1, eps2, objective_of)
        e1_table = exp1_by_variant_objective(rows1, objective_of)
        e2_obj = episode_outcomes(
            eps2, objective_of, lambda e: objective_of.get(e.case_id, "?"), "objective"
        )
        e2_var = episode_outcomes(eps2, objective_of, lambda e: e.variant, "variant")
        e3_tab = episode_outcomes(
            eps3, objective_of, lambda e: objective_of.get(e.case_id, "?"), "objective"
        )
        sugg, examples = taxonomy(session, [args.exp2, args.exp3])
        sens = h2_adjudication_sensitivity(session, args.exp2, eps2)

    safety = []
    for exp in ("exp2", "exp3"):
        for system, m in sorted(res[exp]["by_system"].items()):
            u = m.get("unsafe_proposed_vs_admitted") or {}
            inj = m.get("injection_compliance") or {}
            safety.append({
                "experiment": int(exp[-1]), "system": system, "episodes": m.get("episodes"),
                "unsafe_execution": ((m.get("outcome_rates") or {})
                                     .get("unsafe_execution", {}).get("rate")),
                "unsafe_proposed": u.get("n"), "unsafe_admitted": u.get("total"),
                "unsafe_admitted_share": u.get("rate"),
                "injection_episodes": inj.get("episodes"),
                "injection_proposed": (inj.get("proposed") or {}).get("total"),
                "injection_proposed_rate": (inj.get("proposed") or {}).get("rate"),
                "injection_admitted": (inj.get("admitted") or {}).get("total"),
                "injection_admitted_rate": (inj.get("admitted") or {}).get("rate"),
                "injection_token_echoed": (inj.get("token_echoed") or {}).get("total"),
                "log_deletion_attempts": m.get("log_deletion_attempts"),
                "permitted_wrongly_blocked": (m.get("permitted_wrongly_blocked") or {}).get("rate"),
                "tool_call_accuracy": (m.get("tool_call_accuracy") or {}).get("rate"),
                "invalid_outputs": m.get("invalid_outputs"),
            })  # fmt: skip

    runs = run_rows(ids)
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()  # fmt: skip
    results = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_head_at_export": head,
        "units": "proportion (rates in [0,1], differences in [-1,1]); *_pp fields are display "
                 "values in percentage points",
        "runs": runs,
        "hypotheses": hyp,
        "breakdowns_exploratory": brk,
        "h2_adjudication_sensitivity_exploratory": sens,
        "exp1": {"results": res["exp1"], "by_system_variant_objective": e1_table,
                 "verifier_eval": veval, "retrieval": retrieval},
        "exp2": {"results": res["exp2"], "outcomes": outcomes["exp2"],
                 "by_objective": e2_obj, "by_variant": e2_var},
        "exp3": {"results": res["exp3"], "outcomes": outcomes["exp3"], "by_objective": e3_tab},
        "ablations_exploratory": {"rag_exp1": rag, "recovery_exp2_matched_run_1": recovery,
                                  "mcp_exp2_matched_run_1": mcp},
        "safety": safety,
        "taxonomy": {"human_tags": human_tax, "suggested_counts": sugg,
                     "example_traces": examples,
                     "note": "The six tags are applied by people on the Traces page (none are "
                             "applied yet). 'suggested' counts are the three tags the code "
                             "derives from trace and labels (scoring/taxonomy.py). The ten "
                             "example traces are chosen by the fixed rule in "
                             "scripts/final_results.py, not starred or tagged by a person."},
    }  # fmt: skip
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str))

    note = (
        f"GateBench final results, case set cases-v1 (case_set_hash "
        f"{runs[0]['case_set_hash']}); runs {ids}; generated {results['generated_at']}; "
        "proportions, *_pp display only"
    )
    write_csv("runs.csv", runs, note)
    hrows = []
    for k in ("H1", "H1_C4", "H2"):
        r = dict(hyp[k])
        hrows.append({"hypothesis": k, **r})
    write_csv(
        "hypotheses.csv",
        hrows,
        note + "; cluster bootstrap over scenarios, B 10000, seed 2026, percentile 95% CI",
    )
    write_csv("breakdowns_exploratory.csv", brk, note + "; exploratory subsets, no verdicts")
    write_csv(
        "h2_adjudication_sensitivity.csv",
        [
            {"scenario": "as adjudicated (registered)", **hyp["H2"]},
            {"scenario": "all adjudicated escalations qualifying", **sens["H2"]},
        ],
        note + "; exploratory sensitivity, not the registered analysis",
    )
    write_csv(
        "exp1_by_system_variant_objective.csv", e1_table, note + "; gate decisions pooled over runs"
    )
    vrows = []
    for v, d in veval["diagnostic"].items():
        vrows.append({"verifier_variant": v, "n": d["n"], "exact_accuracy": d["exact_accuracy"],
                      "binary_accuracy": d["binary_accuracy"], "parse_errors": d["parse_errors"],
                      **{f"exact_accuracy_{cv}": x["exact_accuracy"]
                         for cv, x in d["by_case_variant"].items()}})  # fmt: skip
    write_csv("exp1_verifier_accuracy.csv", vrows, note + "; Exp 1V diagnostic, every package")
    rrows = []
    for mode, d in retrieval["modes"].items():
        rrows.append({"mode": mode, **d["overall"]})
    write_csv(
        "retrieval.csv",
        rrows,
        note + "; technique-level proxy relevance; Recall@5 ceiling = mean min(1, 5/|G|)",
    )
    write_csv("exp2_outcomes_by_objective.csv", e2_obj, note + "; episodes pooled over runs")
    write_csv("exp2_outcomes_by_variant.csv", e2_var, note + "; episodes pooled over runs")
    write_csv("exp3_outcomes.csv", e3_tab, note + "; episodes pooled over runs")
    e3m = []
    for system, d in outcomes["exp3"]["exp3"].items():
        e3m.append({"system": system, "episodes": d["episodes"],
                    **{f"technique_{k}": v for k, v in d["technique"].items()},
                    **{f"evidence_{k}": v for k, v in d["evidence"].items()}})  # fmt: skip
    write_csv(
        "exp3_technique_evidence.csv",
        e3m,
        note + "; technique P/R/F1 pooled over episodes; incidental techniques adjudicated (A-15)",
    )

    def flat(group: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for it in items:
            r = it["result"]
            row = {"ablation": group, **it["request"]}
            if "refused" in r:
                out.append(row | {"refused": json.dumps(r["refused"])})
                continue
            out.append(row | {"rate_a": r["rate_a"], "rate_b": r["rate_b"],
                              **{f"diff_{k}": v for k, v in r["difference"].items()},
                              "exploratory": r["exploratory"]})  # fmt: skip
        return out

    write_csv(
        "ablations_exploratory.csv",
        flat("rag", rag) + flat("recovery", recovery) + flat("mcp", mcp),
        note + "; a - b, paired per case, cluster bootstrap",
    )
    write_csv(
        "safety.csv",
        safety,
        note + "; unsafe = prohibited or unapproved calls; admitted share = admitted / proposed",
    )
    write_csv("taxonomy_suggested.csv", sugg, note + "; suggested tags only (not human-applied)")
    write_csv("example_traces.csv", examples, note + "; chosen by a fixed rule")

    fig = OUT / "figures"
    (fig / "hypotheses_forest.svg").write_text(forest(hyp))
    (fig / "exp2_outcomes.svg").write_text(
        outcome_bars(e2_obj, "Exp 2: outcomes per system (217 test cases)", EXP2_ORDER, "objective")
    )
    (fig / "exp3_outcomes.svg").write_text(
        outcome_bars(
            e3_tab,
            "Exp 3: outcomes per system (12 end-to-end windows)",
            ("G0", "G1", "G3"),
            "objective",
        )
    )
    (fig / "exp1_wrongly_admitted.svg").write_text(heatmap(e1_table))
    (fig / "exp2_unsafe_proposed_admitted.svg").write_text(proposed_admitted(safety))
    print(json.dumps({k: {kk: hyp[k].get(kk) for kk in ("estimate", "ci_low", "ci_high")}
                      for k in ("H1", "H1_C4", "H2")}))  # fmt: skip
    print("wrote", OUT)


if __name__ == "__main__":
    main()
