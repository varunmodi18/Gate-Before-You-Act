# STATUS — Gate Before You Act (GateBench)

Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), **Draft 8** (authoritative copy is the one in this repo).
Updated in the same commit that completes a task (plan §L.6).

## Where we are

- **Current milestone:** M0 Foundations and spike, on branch `m0-foundations`.
- **TA approval (Q-0):** approved; recorded 2026-10-08. M0 is cleared; M1 starts only when the team says so.
- **Next action:** T0.3 (model-serving spike) — waiting for the user's OK on download sizes.
- **Stop rule:** stop and report at the end of every milestone and at each team question (Q-0 to Q-5).

## Tasks

Status is one of todo / doing / done / blocked. "PR" is the branch until a PR exists.

| Task | Status | PR | Notes |
|---|---|---|---|
| T0.1 Repository scaffold | done | m0-foundations | Lint and tests pass locally (see Measured numbers). **CI has not yet run on GitHub**: the branch is pushed at the M0 checkpoint |
| T0.2 Store and migrations | done | m0-foundations | All §F.1 tables in Alembic `0001_initial`; WAL + foreign keys + busy timeout on every connection; `make db` |
| T0.3 Early model-serving spike | todo | | Needs user OK before large downloads |
| T0.4 LLM client abstraction | todo | | |
| T1.1 OTRF fetch | todo | | |
| T1.2 Catalogue | todo | | |
| T1.3 Normaliser and field map | todo | | |
| T1.3a SQL guard and hardened connection | todo | | |
| T1.4 Windows API | todo | | |
| T1.5 De-duplication and eligibility | todo | | |
| T1.6 Split selection and freeze | todo | | Team review of the split list |
| T1.7 Windows page | todo | | |
| T2.1 Trusted-context model | todo | | |
| T2.2 Policy engine and rules | todo | | Team sign-off on `rules.yaml` |
| T2.3 Tool layer and registry | todo | | |
| T2.4 Typed-argument rule | todo | | |
| T2.5 Gate checks and orchestrator | todo | | |
| T2.6 Exp 1 core (code-only) | todo | | |
| T2.7 Gate Playground page | todo | | |
| T3.1 Retrieval index | todo | | Q-4 licences |
| T3.2 Verifier prompt and evidence format | todo | | Team review of snapshots |
| T3.3 C4 integration | todo | | |
| T3.4 Configuration completion | todo | | |
| T3.5 Exp 1 runner via worker | todo | | |
| T3.6 Playground with C4, Experiments page | todo | | |
| T3.7 Reranker, retrieval cache, metrics | todo | | |
| T4.1 Case models and files | todo | | |
| T4.2 DB patcher | todo | | |
| T4.3 Variant and prefix builders | todo | | |
| T4.4 Validator | todo | | |
| T4.5 Scenario Studio page | todo | | |
| T4.6 Case-authoring guide | todo | | |
| T4.7 Annotation page | todo | | |
| T4.8 Agreement and adjudication | todo | | |
| T4.9 Annotation execution | todo | | **Team work** — not done by the coding agent |
| T4.10 Freeze | todo | | **Team work** — not done by the coding agent |
| T5.1 Proposer prompt and schema | todo | | |
| T5.2 Episode loop | todo | | |
| T5.3 Outcome classifier | todo | | |
| T5.4 Exp 2 runner | todo | | |
| T5.5 Budget monitor | todo | | |
| T5.6 Agent Console and Traces pages | todo | | R1 sign-off |
| T5.7 Recovery-budget ablation (A7) | todo | | |
| T5.8 MCP tool server and A8 | todo | | One-day time box |
| T6.1 Investigator | todo | | |
| T6.2 Exp 3 runner and metrics | todo | | |
| T6.3 Console for windows | todo | | |
| T7.1 Metrics module | todo | | |
| T7.2 Bootstrap and hypotheses | todo | | |
| T7.3 Sensitivity recalculation | todo | | |
| T7.4 Results page | todo | | |
| T7.5 Error taxonomy and exports | todo | | |
| T8.0 Token audit and budget | todo | | |
| T8.1 Demo fixture | todo | | |
| T8.2 Replay cassettes | todo | | |
| T8.3 Playwright suite | todo | | |
| T8.4 Performance check | todo | | |
| T8.5 Documentation | todo | | |
| T9.1–T9.4 Research runs and analysis | todo | | **Team work** — started by the team, not the coding agent |

## Decisions log

| Date | Decision | Evidence / reason | Plan section |
|---|---|---|---|
| 2026-10-08 | TA approved the final proposal (Q-0 resolved); M0 may proceed | User message at session start ("TA APPROVAL STATUS: APPROVED") | §K.3 Q-0 |
| 2026-10-08 | Node 22 LTS (via nvm) instead of Node 20; plan bumped to Draft 8 | Node 20 is end-of-life; team decision | §G, §J.1, §0.8 |
| 2026-10-08 | §D.5.1 wording: only rows dropped entirely by the 1,500-token limit are unregistered; rows with a field shortened to 200 characters are registered | Otherwise records with long command lines could never be cited (R17); the gate re-reads canonical records anyway. Team decision | §D.5.1, §0.8 |
| 2026-10-08 | Repository layout: base commit on `main` (plan, proposal, README, .gitignore); one branch per milestone; only that base commit may be pushed to `main` | Team instruction | §G (Git) |
| 2026-10-08 | T0.1 counted done on local lint and test runs; GitHub CI runs when `m0-foundations` is pushed at the M0 checkpoint | Team instruction (no pushes before the checkpoint) | T0.1 |
| 2026-10-08 | React 18 kept as in the plan; React Router pinned to 7.x (8.x requires React ≥ 19.2.7); eslint used instead of the template's oxlint | Peer dependency of `react-router@8`; plan §C.3 names eslint | §C.3 |
| 2026-10-08 | DuckDB pinned to 1.5.6 | The layer-2 hardening of §D.5.1 was verified on 1.5.6 | §D.5.1 |
| 2026-10-08 | `app.db` additions beyond §F.1: `verifier_evals.manifest` and `.prompt_hash` (§D.7.1 requires the manifest and prompt hash with every row); CHECK constraints only where the plan enumerates values; `gate_decisions.run_id` and `episodes.run_id/case_id` nullable (Playground and Console calls have no run; Exp 3 window mode) | §D.7.1, §E.1 | §F.1 |
| 2026-10-08 | The CPU reranker (torch, sentence-transformers) is an optional `rerank` extra pulled from the PyTorch CPU index, not installed by `make setup` | Keeps setup small; installed when T3.7 starts | §C.3, §D.3 |

## Measured numbers

### Environment (2026-10-08)

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, driver 580.178.04 (CUDA 13.0); display on iGPU (`prime-select` = on-demand), 15 MiB used at idle |
| RAM / disk | 15 GiB RAM; 51 GB free on `/` |
| Toolchain | uv 0.12.23; Python 3.11.17 (uv-managed); Node 22.23.3 (nvm); pnpm 12.10.1; no `nvcc` (CUDA toolkit) installed |
| Network | PyPI CDN ≈ 0.11 MB/s; Hugging Face ≈ 7.4 MB/s; npm ≈ 0.7 MB/s (single measurements) |

### Test runs

See the latest entry per task.

| Date | Task | Command | Result |
|---|---|---|---|
| 2026-10-08 | T0.1 | `make setup` | OK (uv sync, pnpm install --frozen-lockfile, pre-commit hook installed) |
| 2026-10-08 | T0.1 | `make lint` | ruff check and format: pass; mypy: no issues in 23 files; eslint, prettier, tsc: pass |
| 2026-10-08 | T0.1 | `make test` | pytest 6 passed; vitest 4 passed |
| 2026-10-08 | T0.1 | `make up` + curl | `GET /api/v1/health` → `{"api":"ok"}`; `/` and `/windows` serve the SPA (title GateBench); API listens on 127.0.0.1:8000 only; worker started and stopped on SIGTERM |
| 2026-10-08 | T0.1 | GitHub CI | **not run yet** (branch not pushed) |
| 2026-10-08 | T0.2 | `uv run pytest tests/unit/test_store.py` | 12 passed: upgrade creates the 14 §F.1 tables; downgrade to base and re-upgrade; migrated schema has no diff against the models; WAL and foreign keys on; `job_items`, `verifier_evals` and `annotations` unique keys; CHECK constraints; foreign key enforced |
| 2026-10-08 | T0.2 | `make db` (twice) | `data/app.db` created at revision 0001, journal mode WAL; second run is a no-op |
| 2026-10-08 | T0.2 | `make lint`, `make test` | lint clean (mypy: 29 files); pytest 18 passed; vitest 4 passed |
