# CLAUDE.md — Gate Before You Act (GateBench)

**Start here:** read [STATUS.md](STATUS.md) (current task, decisions, measured numbers), then the
relevant task in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) §H. The plan in this repo is
authoritative; known plan/proposal differences are in §0.7. Any other conflict: stop and ask.

## Working rules

- Follow the task order of plan §L.2; never start a task before its prerequisites are done.
- One task at a time: implement → write the tests listed under "Verification" → run them → mark done.
- Update `STATUS.md` (task table, decisions log, measured numbers) in the same commit that completes a task.
  A deviation from the plan also updates the plan section and bumps its draft number (§L.6).
- Commits start with the task ID (`T0.2: store and migrations`). One branch per milestone (`m0-foundations`, …).
  Never commit `data/` (except `data/fixtures/`, `data/splits.json`), weights, venvs or secrets.
  No push, merge to `main` or force-push unless the user asks.
- `data/splits.json` is the team-accepted split (8 Oct 2026). Changing it needs a new split version and a team decision (plan §D.2 step 5); never regenerate it with a different seed or rule silently. Stage files by name.
- Never use sudo. If a system package is missing, give the user the exact command.
- Never invent results: only numbers and test outcomes actually produced. Say so when something was not run.
- Not for the coding agent: annotation (T4.9), adjudication, freezing (T4.10), starting research runs (T9.x).
- Stop and report at every milestone end and at team questions Q-0…Q-5 (§K.3). After 3 failures of the same step, stop and explain.

## Constraints that must never be weakened (plan §L.4, short form)

1. One gate implementation (`gbya.gate`) for experiments, Playground and Console; no demo special cases.
2. Check semantics exactly per §D.6; first failure decides; C4 is rationale-blind except A3.
3. Trusted context never comes from logs; log values enter only typed slots after validation and provenance.
4. Variants differ only as specified; trusted context identical across E1–E5.
5. Exp 1 feeds every gate the identical package.
6. No LLM judge in scoring; deterministic classification plus human adjudication.
7. Same quantised model file for every system, with its checksum recorded.
8. Analysis refuses replay, unfrozen or pending data; results carry provenance.
9. No real side effects: mock tools, no delete tool, localhost only.
10. No technique ID or ATT&CK tag is ever a retrieval/lookup key; A6 never silently falls back to bm25.
11. One tool implementation behind both transports; the MCP server enforces the gate.
12. Proportions internally (rates in [0,1], differences in [−1,1]); percentage points only in `*_pp` display fields.
    Thresholds 0.15 / 0.10 / 0.10 live only in `gbya/analysis/hypotheses.py`.
13. The gate trusts only canonical records (re-read from DuckDB); provenance only from direct base-table `record_id` projections.
14. **Evidence is never truncated**: over-budget packages are rejected; every verifier input has a manifest; say "selected fields without truncation", never "whole records".
15. Writable DuckDB only via `gbya.data.build_db.open_for_build` (normaliser, patcher). Everything else uses the hardened read-only `gbya.data.connection.open_case_db`.
16. `request_approval` never ends an episode; a cap is a flag, not an outcome; approval rules live only in §D.6.2a.
17. Single-run configurations are compared only on the matched run.

## Commands

```bash
make setup       # uv sync, pnpm install, pre-commit hooks
make lint        # ruff, mypy, eslint, prettier, tsc
make test        # pytest (no GPU; `gpu` marker deselected) + vitest
make up          # build SPA, run API (127.0.0.1:8000, serves SPA) + worker
make api | worker | web
```

Node: `source ~/.nvm/nvm.sh && nvm use 22` (the Makefile does this). Python 3.11 via uv.
Code: `backend/gbya/` (core library, importable without FastAPI), `frontend/` (Vite + React 18 + TS), `tests/`.
