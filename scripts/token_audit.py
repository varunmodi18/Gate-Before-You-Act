"""Real-prompt token audit (plan T8.0, step 1). No model and no GPU: the agent's turns come from
a scripted client, so only the prompts are real.

    make budget        # this audit, then scripts/budget.py

Refused until at least ``--min-dev`` (10) dev scenarios are **adjudicated** (``--force`` skips
the check, e.g. for tests). For the dev scenarios it builds, with the model tokenizer:

* **proposer** turns 1-4 with growing history: a real Exp 2 episode (G1, run 1) per case, the
  scripted agent running three read-only queries and then ending; the step records hold each
  turn's prompt tokens, after the history policy;
* **verifier**: the C4 prompt of every case for every Exp 1 verifier variant
  (``exp1.verifier_input``; the manifest's total); a block over its budget is counted as a
  ``PromptBudgetError``; ``rerank`` is skipped when the reranker is not installed;
* **investigator**: the hypothesis, SQL and technique-verifier prompts of a live investigation
  of each end-to-end window (or, before those exist, each dev window), with the frozen prompts.

Writes ``docs/token_audit.json``: per call type n, mean, p95, max input tokens, the output cap,
and the per-request checks (proposer max ≤ ``PROMPT_LIMIT``, verifier blocks within §D.7.1,
investigator max + output ≤ the model's context).
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "token_audit.json"
CONTEXT = 8192  # --max-model-len of the vLLM profile
QUERIES = ["SELECT * FROM process_create LIMIT 20", "SELECT * FROM process_access LIMIT 20",
           "SELECT * FROM network LIMIT 20"]  # fmt: skip


def stats(xs: list[int]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "mean": None, "p95": None, "max": None}
    s = sorted(xs)
    p95 = s[max(0, math.ceil(0.95 * len(s)) - 1)]
    return {"n": len(s), "mean": round(sum(s) / len(s), 1), "p95": p95, "max": s[-1]}


class ScriptedAgent:
    """Three read-only queries, then ``end_episode`` (the turns grow the history)."""

    def __init__(self) -> None:
        self.turn = 0

    def chat_json(self, messages: Any, schema: Any, *, temperature: float, seed: int | None,
                  max_tokens: int) -> tuple[dict[str, Any], Any]:  # fmt: skip
        from gbya.llm.schemas import Usage

        self.turn += 1
        if self.turn <= len(QUERIES):
            out = {
                "thought": "token audit: read-only query",
                "tool": "sql_query",
                "args": {"sql": QUERIES[self.turn - 1]},
                "cited": [],
                "technique_id": None,
            }
        else:
            out = {
                "thought": "token audit: end",
                "tool": "end_episode",
                "args": {"reason": "cannot_proceed"},
                "cited": [],
                "technique_id": None,
            }
        return out, Usage(prompt_tokens=0, completion_tokens=0)


class CapturingInvestigatorClient:
    """Minimal valid investigator outputs; records each prompt's tokens by step."""

    def __init__(self, counter: Any) -> None:
        self.counter = counter
        self.tokens: dict[str, list[int]] = defaultdict(list)

    def chat_json(self, messages: Any, schema: Any, *, temperature: float, seed: int | None,
                  max_tokens: int) -> tuple[dict[str, Any], Any]:  # fmt: skip
        from gbya.llm.schemas import Usage

        text = "\n".join(str(m["content"]) for m in messages)
        props = schema.get("properties", {})
        if "hypotheses" in props:
            kind, out = "hypothesise", {"thought": "audit", "hypotheses": [
                {"technique_id": t, "behaviour": "audit"}
                for t in ("T1003.001", "T1059.001", "T1547.001")]}  # fmt: skip
        elif "queries" in props:
            kind, out = "sqlgen", {"queries": [{"sql": q, "purpose": "audit"} for q in QUERIES[:2]]}
        else:
            kind, out = "technique_verifier", {"verdict": "REJECT", "cited": [], "reason": "audit"}
        self.tokens[kind].append(self.counter.count(text))
        return out, Usage(prompt_tokens=0, completion_tokens=0)


def _ready(session: Any, min_dev: int) -> list[str]:
    from sqlalchemy import select

    from gbya.store.models import Scenario

    rows = list(session.scalars(select(Scenario).where(Scenario.split == "dev")))
    done = [r.id for r in rows if r.status in ("adjudicated", "frozen")
            and (r.spec or {}).get("kind") != "end_to_end"]  # fmt: skip
    if len(done) < min_dev:
        raise SystemExit(
            f"T8.0 needs {min_dev} adjudicated dev scenarios; {len(done)} are "
            f"({sorted(done)}). Adjudicate them first (or --force for a test)."
        )
    return sorted(done)


def audit(settings: Any, counter: Any, *, min_dev: int = 10, force: bool = False,
          scenarios: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    from sqlalchemy import select

    from gbya.agent import investigator as iv
    from gbya.agent import proposer as pp
    from gbya.data.connection import open_case_db
    from gbya.experiments import exp2
    from gbya.experiments.exp1 import load_case, verifier_input
    from gbya.experiments.runner import Deps
    from gbya.experiments.runner import load_spec as load_exp1_spec
    from gbya.gate import verifier as vf
    from gbya.retrieval.index import RetrievalError
    from gbya.store import db
    from gbya.store.models import Case, Scenario
    from gbya.tools.registry import ToolEnvironment
    from gbya.tools.state import EpisodeState

    factory = db.make_sessionmaker(db.make_engine(settings.resolve(settings.app_db_path)))
    with factory() as s:
        if force:
            sids = scenarios or sorted({c.scenario_id for c in s.scalars(
                select(Case).where(Case.set_ != "X"))})  # fmt: skip
        else:
            sids = scenarios or _ready(s, min_dev)
        cases = [c.id for c in s.scalars(select(Case).where(Case.scenario_id.in_(sids)))
                 if c.set_ != "X"]  # fmt: skip
        x_cases = [c for c in s.scalars(select(Case).where(Case.set_ == "X"))]
        chosen = x_cases or [c for c in s.scalars(select(Case).where(Case.id.in_(cases)))
                             if c.variant == "E1"]  # fmt: skip
        windows = sorted({(str(c.case_db_path), str(c.request["text"]),
                           json.dumps(s.get(Scenario, c.scenario_id).trusted_context))  # type: ignore[union-attr]
                          for c in chosen})  # fmt: skip
    proposer: dict[int, list[int]] = defaultdict(list)
    slot = exp2.load_spec().schedule[1]
    for cid in sorted(cases):
        with factory() as s:
            try:
                case = exp2.load_episode_case(s, cid, settings)
            except ValueError:
                continue  # no prefix
        deps = Deps(settings=settings, client=ScriptedAgent(), counter=counter)  # type: ignore[arg-type]
        trace = exp2.run_episode(case, deps.configs["G1"], slot, deps, factory)
        for kind, p, _ in trace.steps:
            if kind == "llm" and p.get("prompt_tokens") is not None:
                proposer[int(p["call"])].append(int(p["prompt_tokens"]))
    verifier: dict[str, list[int]] = defaultdict(list)
    budget_errors: dict[str, int] = defaultdict(int)
    skipped: dict[str, str] = {}
    variants = load_exp1_spec().verifier_variants
    deps = Deps(settings=settings, client=ScriptedAgent(), counter=counter)  # type: ignore[arg-type]
    for cid in sorted(cases):
        with factory() as s:
            case = load_case(s, cid, settings)
        for variant in variants:
            if variant in skipped:
                continue
            try:
                prompt, _, _ = verifier_input(case, deps.verifier(variant, cid, factory), counter)
            except vf.PromptBudgetError:
                budget_errors[variant] += 1
                continue
            except RetrievalError as exc:  # e.g. the reranker is not installed
                skipped[variant] = str(exc)
                continue
            verifier[variant].append(int(prompt.manifest["tokens"]["total"]))
    cap = CapturingInvestigatorClient(counter)
    has_index = (settings.resolve(settings.data_dir) / "index" / "MANIFEST.json").is_file()
    for path, request, context in windows:
        con = open_case_db(Path(path))
        try:
            from gbya.context.models import TrustedContext

            ctx = TrustedContext.model_validate(json.loads(context))
            tools = ToolEnvironment(con=con, context=ctx, state=EpisodeState(), counter=counter)
            iv.Investigator(client=cap, tools=tools, counter=counter,  # type: ignore[arg-type]
                            retrieve=vf.live_retriever(deps.index()) if has_index else None,
                            candidates=iv.attack_candidates(deps.index()) if has_index else None,
                            temperature=0.2, seed=11, request_text=request).run(con)  # fmt: skip
        finally:
            con.close()
    prop_all = [t for v in proposer.values() for t in v]
    inv_all = [t for v in cap.tokens.values() for t in v]
    out = {
        "tokenizer": counter.name, "scenarios": sids, "cases": len(cases),
        "investigated_windows": len(windows),
        "proposer": {"by_turn": {str(k): stats(v) for k, v in sorted(proposer.items())},
                     "all": stats(prop_all), "output_cap": pp.MAX_TOKENS,
                     "limit": pp.PROMPT_LIMIT},
        "verifier": {"by_variant": {k: stats(v) for k, v in sorted(verifier.items())},
                     "all": stats([t for v in verifier.values() for t in v]),
                     "output_cap": vf.MAX_TOKENS, "budget_errors": dict(budget_errors),
                     "skipped": skipped},
        "investigator": {"by_step": {k: stats(v) for k, v in sorted(cap.tokens.items())},
                         "all": stats(inv_all), "output_caps": iv.MAX_TOKENS,
                         "context": CONTEXT},
    }  # fmt: skip
    pmax = out["proposer"]["all"]["max"] or 0
    imax = max((stats(v)["max"] or 0) + iv.MAX_TOKENS[k] for k, v in cap.tokens.items()) \
        if cap.tokens else 0  # fmt: skip
    out["checks"] = {
        "proposer_within_limit": pmax <= pp.PROMPT_LIMIT,
        "verifier_within_budget": not budget_errors,
        "investigator_within_context": imax <= CONTEXT,
    }
    return out


def main(argv: list[str] | None = None) -> None:
    from gbya.config import get_settings
    from gbya.llm.tokens import ApproxCounter, default_counter

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min-dev", type=int, default=10)
    ap.add_argument("--force", action="store_true", help="skip the adjudicated-dev check")
    ap.add_argument("--approx", action="store_true", help="approximate counter (tests only)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    counter = ApproxCounter() if args.approx else default_counter()
    result = audit(get_settings(), counter, min_dev=args.min_dev, force=args.force)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["checks"]), "->", args.out)


if __name__ == "__main__":
    main()
