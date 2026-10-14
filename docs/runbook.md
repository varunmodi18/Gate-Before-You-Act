# Runbook — experiment operations

For the team lead, who starts the research runs (plan T9.x). The team's own workflow (authoring,
annotation, the dated checklist) is in `docs/TEAM_GUIDE.md`; the decisions are in `STATUS.md`.
Research runs never start while annotation is in progress.

## 1. Before the first research run

1. **Frozen case set** — `make freeze-cases VERSION=cases-v1` (refuses with a list of what is
   missing: adjudication, validator all-pass under the model tokenizer, exclusions, the context
   check of every e2e window, injection markers). Commit `cases/` and tag the version.
2. **Pilot and sensitivity** — partial Exp 1 and Exp 2 runs on dev scenarios (purpose
   `development`); `uv run python scripts/sensitivity.py --exp1 <run> --exp2 <run>`; record the
   numbers in `STATUS.md`.
3. **T8.0 budget** — real-prompt token audit and regenerated budget; if it exceeds the hours
   available, apply fallback F1 (`experiments/fallbacks.yaml`) and record it first.
4. **Retrieval cache** — `make retrieval-cache` on the frozen set, **before** starting the model
   server (the reranker runs on the CPU and needs the RAM).
5. **Model server** — `make model-up` (profile `vllm-awq`); the Home page's model card is green;
   `make diag` shows the model server and the frozen case set. The model file's checksum is
   recorded with every run.

## 2. Running an experiment

Experiments page → new run → purpose **research** (or `POST /api/v1/runs` with
`{"experiment": 1|2|3, "purpose": "research"}`; specs in `experiments/exp{1,2,3}.yaml`). A research
run is refused unless every case's content hash is in `cases/FROZEN.json` and the token counter
is the model tokenizer; a replay or Fake backend can never make a research run.

| Experiment | Systems and runs | Notes |
|---|---|---|
| Exp 1 (H1, H1-C4) | G0, G1, G2, A1 code-only; G3, A2, A3, A4, A6 composed from Exp 1V (3 verifier runs) | Exp 1V verifier calls first; retrieval from the cache |
| Exp 2 (H2) | G0, G1, G3 × 3 runs; G2, A1, A5, A7, A8 × run 1 (or F1) | episodes; the adjudication queue must end empty |
| Exp 3 | G0, G1, G3 × 2 runs on the 12 e2e windows | live investigation; incidental techniques go to the queue |

**Watch** the run page: progress (SSE), errors, tokens/s, ETA, and the budget monitor (it warns
when the projection exceeds the budget and offers F1). **Stop / resume** with the buttons (or
`/runs/{id}/cancel`, `/resume`): units are atomic, so a resumed run never duplicates work; a
killed worker is restarted with `make worker` and resumes. Run overnight on AC power.

## 3. Adjudication queue (Exp 2 and Exp 3)

*Adjudication queue* page: unlisted calls (permitted with fulfils / requires approval, or
prohibited), unlisted escalations (qualifying or not) and, for Exp 3, predicted techniques
outside the window's metadata (incidental or not). One decision re-scores every episode of the
case; the system is hidden. Analysis is refused while the queue is not empty.

## 4. Analysis

- **Results page** → choose the Exp 1 run, then the Exp 2 research run → *Compute* (bootstrap
  B = 10,000, seed 2026): H1, H1-C4, H2 cards, forest plot, the exploratory code-ticket table,
  provenance. `POST /api/v1/analysis/hypotheses` returns 422 for replay, non-research, unfinished,
  unfrozen or pending data.
- **Comparisons** (capability ablations, exploratory): `POST /api/v1/analysis/compare` with
  `matched_run: 1` whenever the systems have different run counts (A5, A7, A8 vs G3 run 1).
- **Metrics per run**: `GET /api/v1/runs/{id}/results` (FR-21), `/verifier-eval` (Exp 1V),
  `/retrieval` (RAG metrics), `/outcomes` (Exp 2 / Exp 3, technique F1).
- Demo or unfrozen runs: illustrative mode only (`/analysis/illustrative`; no verdicts).
- Exports (CSV/JSON) and the error taxonomy are task T7.5 (not built yet).

## 5. Demo record and replay

`make demo-record` (demo with the live model, every call appended to
`data/fixtures/demo/replay/demo.jsonl`), then `uv run python scripts/record_replay.py`; commit
the cassette. `make demo-replay` serves the demo from it without a model (header REPLAY; runs
flagged replay). A request outside the recorded script fails with `REPLAY_MISS`.

## 6. Troubleshooting

`make diag`, the logs in `logs/`, and the table in `README.md`. A run item that fails three
times is marked `error` with its trace; resume after fixing the cause. Never edit `app.db`,
case files or `FROZEN.json` by hand.
