# STATUS — Gate Before You Act (GateBench)

Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), **Draft 8** (authoritative copy is the one in this repo).
Updated in the same commit that completes a task (plan §L.6).

## Where we are

- **Current milestone:** M1 Data walking skeleton, on branch `m1-data` (from `m0-foundations`). M0 complete; `m0-foundations` pushed.
- **TA approval (Q-0):** approved; recorded 2026-10-08. M1 approved by the team on 2026-10-08.
- **Next action:** T1.2 (catalogue), then T1.2 → T1.3 → T1.3a → T1.4 → T1.7, with T1.5 → T1.6 in parallel. Stop at the M1 checkpoint.
- **Model server:** `make model-up` (profile `vllm-awq`), then `make gpu-test` / `make pilot`. It is stopped when not in use.
- **Stop rule:** stop and report at the end of every milestone and at each team question (Q-0 to Q-5).

## Tasks

Status is one of todo / doing / done / blocked. "PR" is the branch until a PR exists.

| Task | Status | PR | Notes |
|---|---|---|---|
| T0.1 Repository scaffold | done | m0-foundations | Lint and tests pass locally (see Measured numbers). **CI has not yet run on GitHub**: the branch is pushed at the M0 checkpoint |
| T0.2 Store and migrations | done | m0-foundations | All §F.1 tables in Alembic `0001_initial`; WAL + foreign keys + busy timeout on every connection; `make db` |
| T0.3 Early model-serving spike | done (known issue) | m0-foundations | `docs/pilot_report.md`. FP16 KV accepted (deviation from proposal §13). Known issue: 78/80 schema-valid (soak 459/480; with repetition_penalty 1.05: 73/80), all failures are repetition loops hitting `max_tokens`; T5.1 must reach ≥ 99%. F1 not triggered |
| T0.4 LLM client abstraction | done | m0-foundations | Live (httpx, retries 2/8/30 s), Fake (hash or regex rules), Replay + recorder; 13 unit tests and the `gpu` live smoke test pass against the `vllm-awq` profile |
| T1.1 OTRF fetch | done | m1-data | `make data-fetch`: HEAD `d9d40ef123d2c87d5d3df28c96bcab4f0faccc87`, 100 SDWIN metadata files, 165 Windows data files, 207 MB |
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
| T5.1 Proposer prompt and schema | todo | | Acceptance check from T0.3: ≥ 99% schema-valid on the synthetic 20-episode pilot run with the real proposer schema |
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
| 2026-10-08 | vLLM 0.31.0 installed in a separate venv: torch, torchvision, torchaudio, torchcodec, Triton, `cuda-toolkit` and 14 NVIDIA wheels from the official PyTorch index (cu130; NVIDIA entries served from `pypi.nvidia.com`), the rest from PyPI; versions exactly as vLLM 0.31.0 resolves (198 packages, `config/vllm-requirements.lock`) | Team instruction; GPU check (matmul + Triton JIT) passed before the PyPI part | T0.3 |
| 2026-10-08 | The vLLM venv lives at `~/.local/share/gbya/venv-vllm`; `.venv-vllm` is a symlink to it | FlashInfer's JIT build passes include paths unquoted; the repo path has spaces (`nvcc fatal: A single input file is required`). The venv was copied and its 58 `bin/` launchers rewritten; package list identical | T0.3, §0.8 |
| 2026-10-08 | `CUDA_HOME=/usr/local/cuda-13.0` for the model server | System nvcc 13.0 matches the driver's CUDA 13.0 | T0.3 |
| 2026-10-08 | **FP16 KV cache (`--kv-cache-dtype auto`) instead of FP8 — accepted by the team as a deviation from proposal §13** | FP8 KV garbled the output at all prompt lengths tried (452–4,418 tokens); FP16 with no other change was coherent. Probe table in `docs/pilot_report.md` and plan §0.8. Same model file. Consequence: the KV cache holds 21,520 tokens, about 2.6 requests of 8,192 tokens at once (vLLM: 2.63×), so long requests queue | T0.3, §0.8; proposal §13 |
| 2026-10-08 | `--generation-config vllm` kept (team decision) | Otherwise the model's `generation_config.json` silently sets top_p 0.8, top_k 20 and repetition_penalty 1.05 for every request; with it only per-request parameters apply | T0.3, §0.8 |
| 2026-10-08 | `repetition_penalty` 1.05 **not** added to the request defaults | Team rule: add it only if the invalid rate drops to about 1% or less. Re-run of the 20 synthetic episodes with 1.05: 73/80 valid (7 hit `max_tokens`) vs 78/80 without | T0.3 |
| 2026-10-08 | T0.3 passed with a known issue; T5.1 gains an acceptance check: ≥ 99% schema-valid on the same synthetic run with the real proposer schema | Team decision | T0.3, T5.1 |
| 2026-10-08 | `uv cache prune` run | Freed 0.9 GB (183 files); 8.3 GB of cache is still referenced by the installed environments (`uv cache clean` would remove it, at the cost of slow re-downloads from PyPI) | — |
| 2026-10-08 | `--structured-outputs-config '{"backend": "xgrammar", "disable_any_whitespace": true}'` | Without it the model emitted only whitespace inside the JSON until `max_tokens` (risk R7). vLLM accepts the option only with an explicit backend | T0.3, §0.8 |
| 2026-10-08 | Model fetched with `scripts/fetch_model.py` at a pinned commit; each file's SHA-256 checked against Hugging Face and stored in `models/<name>/MANIFEST.json` | §L.4 item 7 (same file, recorded checksum) | T0.3 |
| 2026-10-08 | Fallback F1 **not** set | Measured 1,229 input tok/s and 55.4 output tok/s; neither is below half of the 800 / 80 assumption | T0.3, §F.8 |
| 2026-10-08 | LLM retries: 3 retries after the first attempt, waiting 2, 8 and 30 s (4 attempts in total); 4xx responses are not retried | §F.8 lists three delays; read as three retries | T0.4, §F.8 |
| 2026-10-08 | OTRF fetched by full SHA `d9d40ef123d2c87d5d3df28c96bcab4f0faccc87` (`git init` + `fetch --depth 1 --filter=blob:none <sha>` + sparse checkout) instead of `clone --depth 1` | A depth-1 clone only contains the branch tip; fetching the pinned SHA directly gets exactly that commit. Full SHA resolved from the GitHub commit page (API rate-limited) | §D.1 step 1 |
| 2026-10-08 | The CPU reranker (torch, sentence-transformers) is an optional `rerank` extra pulled from the PyTorch CPU index, not installed by `make setup` | Keeps setup small; installed when T3.7 starts | §C.3, §D.3 |

## Measured numbers

### Environment (2026-10-08)

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4060 Laptop GPU, 8188 MiB, driver 580.178.04 (CUDA 13.0); display on iGPU (`prime-select` = on-demand), 15 MiB used at idle |
| RAM / disk | 15,606 MB RAM; 51 GB free on `/` at the start, 27 GB after T0.3 (vLLM venv 8.0 GB, model 5.2 GB, uv cache 9.2 GB); 29 GB after `uv cache prune` (cache 8.3 GB) |
| Toolchain | uv 0.12.23; Python 3.11.17 (uv-managed); Node 22.23.3 (nvm); pnpm 12.10.1; CUDA 13.0 toolkit at `/usr/local/cuda-13.0` (nvcc 13.0.48), **not on PATH** — the session-start note "no nvcc" was wrong |
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
| 2026-10-08 | T0.3 | GPU check in the vLLM venv (`scripts/gpu_check.py`) | PASS: torch 2.13.0+cu130 sees the RTX 4060 (cc 8.9); fp16 matmul matches the CPU result; a Triton 3.7.1 JIT kernel compiles and runs |
| 2026-10-08 | T0.3 | `uv pip check` + comparison with vLLM's resolution | 198 packages, identical to the resolution; all compatible |
| 2026-10-08 | T0.3 | Server starts | Attempt 1: `ninja` not on PATH. Attempt 2: FlashInfer JIT broke on the path with spaces. Attempt 3 (venv moved, CUDA_HOME set): up. Two later restarts failed on launcher/config errors (`str.format` on the JSON argument; whitespace option needs an explicit backend), then up. FP16-KV diagnostic: first start failed (cold start left 0.36 GiB for KV, 0.44 GiB needed), second start up |
| 2026-10-08 | T0.3 | `make pilot` (20 episodes + long request + 30-min soak), profile `vllm-awq` with FP16 KV | 80/80 calls OK, **78/80 schema-valid**; 1,229.2 input tok/s, 55.4 output tok/s; p50/p95 latency 14.0/19.0 s; peak VRAM 7,249 MiB; peak host RAM 8,201 MB; long request 7,850-token prompt OK; soak 30.7 min: 480 calls, 459 valid, input 1,161 tok/s mean (min 1,030), output 56.0 (min 53.0), max 80 °C, only the SW power-cap throttle flag |
| 2026-10-08 | T0.4 | `uv run pytest tests/unit/test_llm_client.py` | 13 passed (Fake, Live with mock transport incl. retries and 4xx, Replay and recorder) |
| 2026-10-08 | T0.4 | `make gpu-test` against the `vllm-awq` profile | 1 passed (live JSON-schema call) |
| 2026-10-08 | M0 | `make lint`, `make test` | lint clean (mypy: 34 files); pytest 31 passed (1 `gpu` deselected); vitest 4 passed |
| 2026-10-08 | T0.3 | `scripts/pilot.py --repetition-penalty 1.05` (20 episodes, no soak) | 80/80 calls OK, **73/80 schema-valid** (7 ended at `max_tokens`); mean completion 231.4 tokens; 1,168.0 input / 60.9 output tok/s; p50/p95 13.65/23.84 s |
| 2026-10-08 | T1.1 | `uv run pytest tests/unit/test_fetch.py` | 4 passed (local git origin: pinned commit and sparse paths only; re-fetch no-op; prefix mismatch refused) |
| 2026-10-08 | T1.1 | `make data-fetch` (twice) | HEAD d9d40ef…; 100 SDWIN metadata files; 4 min 6 s for 207 MB; second run reports already present |

### T0.3 pilot (synthetic prompts; details in `docs/pilot_report.md`)

| Metric | Value |
|---|---|
| Profile | `vllm-awq`: vLLM 0.31.0, Qwen2.5-7B-Instruct-AWQ @ `b2503754`, ctx 8192, util 0.90, **FP16 KV** (21,520 tokens ≈ 2.6 × 8,192-token requests), 4 seqs |
| Input / output throughput (20 episodes, 4 concurrent) | 1,229.2 / 55.4 tok/s (assumed ≥ 800 / ≥ 80) |
| Measured wall vs budget formula `in/800 + out/80` | 288.7 s vs 643.6 s (0.45×) |
| Schema-valid | 78/80 calls (20 episodes); 459/480 in the soak; 73/80 with repetition_penalty 1.05 |
| Peak VRAM / host RAM | 7,249 MiB / 8,201 MB |
| 30-min soak | 30.7 min; input 1,161 tok/s mean; output 56.0 tok/s mean; max 80 °C; no thermal throttling flag |
