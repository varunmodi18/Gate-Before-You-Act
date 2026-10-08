# STATUS — Gate Before You Act (GateBench)

Plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), **Draft 8** (authoritative copy is the one in this repo).
Updated in the same commit that completes a task (plan §L.6).

## Where we are

- **Current milestone:** M2 Deterministic gate, on branch `m2-gate` (from `m1-data`). M0 and M1 complete and pushed.
- **TA approval (Q-0):** approved 2026-10-08. M2 approved by the team on 2026-10-08.
- **Next action:** waiting for the team's sign-off of `policy/rules.yaml` and `policy/evidence_requirements.yaml` (T2.2). Then T2.5 → T2.7 and the M2 checkpoint. Then stop: T2.5 needs T2.2 done, i.e. the team's sign-off on the policy files.
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
| T1.2 Catalogue | done | m1-data | `make catalogue`: 100 windows, checksum `da6d27b0…` identical on re-run. **1 window (SDWIN-230718150800, "Dumping NTDS.dit from Volume Shadow Copy") has its Host file missing at the pinned commit** — catalogued as `missing_host_file` (team question at the M1 checkpoint). The API listing is checked in T1.4 |
| T1.3 Normaliser and field map | done | m1-data | `make normalise`: 99 windows ingested (757,375 events, 0 skipped lines), 1 `missing_host_file`; 2.3 GB of DuckDB files, mode 0444; 35 s. Field map and coverage in `docs/fieldmap.md` |
| T1.3a SQL guard and hardened connection | done | m1-data | `gbya/tools/sql_guard.py` (layer 1 + 2 s watchdog) and `gbya/data/connection.py` (`open_case_db`, layer 2). Both suites pass on DuckDB 1.5.6 |
| T1.4 Windows API | done | m1-data | `GET /windows` (split/tactic/q filters), `GET /windows/{id}`, `GET /windows/{id}/tables/{table}` (pagination, `column:text` filters), `GET /windows/{id}/records/{record_id}`, `POST /windows/{id}/query` (shared guard). Real app.db: lists 100 windows (closes the T1.2 check) |
| T1.5 De-duplication and eligibility | done | m1-data | `make dedup`: 99 ingested windows → 94 groups (5 pairs with J > 0.5); `windows.dedup_group` set for all 99. Eligibility (reading A, see decisions): 99/99 eligible; only 37 are single-host. Report: `data/dedup_report.json` |
| T1.6 Split selection and freeze | done | m1-data | **Accepted by the team 2026-10-08.** `data/splits.json` (seed 2026; dev 10 / test 40 / e2e 12 / unused 37) committed with `docs/splits.md` (every window: title, tactic, split, de-dup group, primary host and its share; tactic counts; acting users for dev + test). Assignment unchanged from the reviewed version after adding the host fields |
| T1.7 Windows page | done | m1-data | `/windows` (tactic/split/text filters) and `/windows/:id` (tab per table, server pagination, `column contains` filter, record drawer with normalised + raw JSON, SQL console with typed errors). Playwright J1 + axe pass at 1280 and 768 px; checked on the real LSASS window |
| T2.1 Trusted-context model | done | m2-gate | `gbya/context/{models,store}.py`: §F.3 schema with validators, canonical-JSON SHA-256 `context_hash`, `get_context(section)` |
| T2.2 Policy engine and rules | blocked | m2-gate | Engine done and tested (`gbya/policy/engine.py`). **Waiting for team sign-off** of the DRAFT `policy/rules.yaml` (plan §D.8 example P1–P8 verbatim) and `policy/evidence_requirements.yaml` (plan wording for isolate_host, same pattern for the others). T2.5 cannot start before this |
| T2.3 Tool layer and registry | done | m2-gate | `gbya/tools/{registry,provenance,render,escalation,mock_actions,state,names}.py`, `gbya/llm/tokens.py`. AST-based retrieved-record registry, untrusted rendering with the model tokenizer, 9 tool schemas, unknown/delete counting |
| T2.4 Typed-argument rule | done | m2-gate | `gbya/tools/typed.py` (validators), `gbya/tools/provenance.py` (typed canonical fields), `gbya/gate/checks.py` (`check_c1`, `check_schema`), `gbya/gate/types.py` (`CheckResult`) |
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
| 2026-10-08 | Catalogue maps each metadata link (which points at OTRF `master`) to the same path in the pinned checkout; never fetches from `master` | Reproducibility at d9d40ef | §D.1 step 2 |
| 2026-10-08 | SDWIN-230718150800 kept in the catalogue with `ingest_status = missing_host_file`; **not** mapped to the unreferenced look-alike file `cmd_dumping_ntds_dit_file_volume_shadow_copy.zip` | Its metadata names `cmd_copy_ntds_from_volume_shadow_copy.zip`, which is absent at d9d40ef. Mapping a different file would be a guess; team question | §D.1, T1.2, T1.3 |
| 2026-10-08 | Tactics stored as ATT&CK IDs (e.g. TA0006) as in the metadata; `TACTIC_NAMES` maps them to short names for filtering | The metadata uses IDs | §F.1 `windows` |
| 2026-10-08 | Plan §D.1 [V] says 99 of 100 datasets map to one technique; measured: 98 have one mapping entry, SDWIN-201022042947 has 4 techniques and SDWIN-210611210814 has 2 (T1134.001, T1134.002). All are stored | Survey of the 100 files at d9d40ef | §D.1 edge cases |
| 2026-10-08 | T1.2 marked done on the database check; its "Windows API lists them" is verified in T1.4, which comes later in §L.2 | Ordering in the plan | T1.2, T1.4 |
| 2026-10-08 | Normaliser reads 4 collection formats (nxlog, `TimeCreated` UTC, `TimeCreated` local, old Winlogbeat flattened from `event_data`) | Field survey of all 99 windows (`docs/fieldmap.md`) | §D.1, §F.2 |
| 2026-10-08 | PID rule: in nxlog events `ProcessID` and `ExecutionProcessID` are never used (they are the writer's / Sysmon's own PID); when `ProcessId` is absent the PID is read from the event's own `Message`, else NULL | Real events show nxlog `ProcessID: 4` vs `Process ID: 716` in the message; using it would give C3 a wrong PID role | §D.5.2, §D.6.2 (PID roles) |
| 2026-10-08 | `channel` stored with canonical spelling (`Security`, `Microsoft-Windows-Sysmon/Operational`); original in `raw_events.json` | Exports use both `Security` and `security` | §F.2 |
| 2026-10-08 | Users: besides lower-case and `DOMAIN\` stripping, `-` and empty become NULL. 4688 `user` = `TargetUserName`, else `SubjectUserName` | `-` means "no account" in Windows logs | §F.2 |
| 2026-10-08 | Security 4656/4663 mapped only for `ObjectType` Process (→ `process_access`) and File (4663 → `file`); other object types stay in `raw_events` | §F.2 names only process and file objects | §F.2 |
| 2026-10-08 | `__MACOSX/` and `._*` archive members excluded (not counted as skipped lines) | They are macOS resource forks in 14 zips, not event data | §D.1 failure behaviour |
| 2026-10-08 | Each window DuckDB file also has a `_meta` table (window id, OTRF commit, source files, counts, ordering, timestamp sources, hosts) | Provenance of each built database; not part of §F.2's 7 tables + `raw_events` | §F.2 |
| 2026-10-08 | Database build: written to `<id>.duckdb.building`, closed, chmod 0444, then renamed into place | A half-built file is never visible under the final name | §D.1.1 |
| 2026-10-08 | SQL guard: only `SELECT` and `UNION` of selects (INTERSECT/EXCEPT rejected, as §D.5.1 names only UNION); table functions, qualified names and quoted table identifiers rejected; DML/DDL searched for in the whole tree (catches DML inside CTEs); accepted SQL regenerated from the AST before wrapping, so comments cannot escape the wrapper | §D.5.1; sqlglot 30.21 parses `WITH d AS (DELETE …) SELECT` as a Select and `FROM read_csv(...)` as a table | §D.5.1 |
| 2026-10-08 | Function denylist = §D.5.1 list (`read_*`, copy, attach, install, load, pragma, system, getenv) plus `glob`, `query`, `query_table`, `sniff_csv`, `current_setting`, `getvariable`, `set_variable`, `iceberg_scan`, `delta_scan` and the prefixes `parquet_`, `duckdb_`, `pragma_` | These also read files, run SQL text or expose engine settings | §D.5.1 |
| 2026-10-08 | `raw_events` is on the query whitelist (agent may search raw JSON); `_meta` is not | Provenance table is not log data | §D.5.1 |
| 2026-10-08 | Added `GET /windows/{id}` and `GET /windows/{id}/records/{record_id}` (normalised row + original JSON) to the §F.5 contract | The Windows page's record drawer (§E.1 row 2) needs them | §F.5 |
| 2026-10-08 | Table filters are `filter=column:text` (case-insensitive contains, ANDed, column names validated, value bound as a parameter) | §F.5 names `filter=` without a format | §F.5 |
| 2026-10-08 | FastAPI validation errors (422) and unknown routes (404) also use the §F.6 envelope (`VALIDATION_ERROR`, `NOT_FOUND`) | One error shape for the UI | §F.6 |
| 2026-10-08 | `open_case_db` passes the six hardening settings as connection-time `config` instead of `SET` statements (plan §D.5.1 updated in Draft 8) | Found in T1.5: DuckDB shares one instance per file in a process, so a second `open_case_db` of the same file failed on the locked configuration (would break concurrent API requests). New tests: repeated and 8 concurrent opens succeed; all layer-2 attacks still blocked; an unhardened connection to the same file is refused while a hardened one is open | §D.5.1 |
| 2026-10-08 | **Eligibility reading A (team to confirm at the T1.6 review):** "≥1 event in process_create or process_access on a single primary host" read as "designate one primary host per window (most process_create + process_access events; ties → more events, then name) and require ≥1 such event on it", plus ≥30 events. Result: 99/99 eligible. Reading B ("all events on one host") would leave only 37 windows (< 62 needed → Q-2) | §D.2 step 1 is ambiguous; 63 of 99 windows were collected from 2–5 hosts | §D.2, FR-03, Q-2 |
| 2026-10-08 | Signature per table = (event_id, image, normalised command line, parent image, target), with image/target: process_create image/–; process_access source_image/target_image; network image/`dst_ip:dst_port`; registry image/target_object; file image/target_filename; logon process_name/target_user; share_access –/`share\\relative_target`. Normalisation: lower-case; GUID → `<guid>`; `0x…` → `<hex>`; component under a Temp folder → `<tmp>`; digit runs > 4 → `<n>` | §D.2 step 2 names the tuple but not the per-table fields | §D.2 |
| 2026-10-08 | Observation for the team: the 5 grouped pairs are different techniques recorded in the same lab sessions (e.g. "Lsass Memory Dump via Comsvcs.dll" ↔ "Windows Vault Web Credentials", J = 0.508), i.e. shared background events, not replays. Grouping only keeps them in the same split, so it is conservative; median pairwise J is 0.026 and 22 of 4,851 pairs exceed 0.4 | Measured on all 99 windows | §D.2 step 3 |
| 2026-10-08 | Split method: seed 2026; strata = primary tactic (first tactic of the first ATT&CK mapping) of a group's smallest-id window; seeded shuffle within strata, round-robin interleave → seeded order (also the replacement queue); per-split per-tactic quotas by largest remainder; each group to the feasible split with the largest unmet quota for its tactic; a backward reachability table guarantees an exact 10/40/12 fill whenever one exists | §D.2 step 4 gives the goals (seeded, stratified, whole groups, exact counts) but not the algorithm. A first greedy version failed the property test (missed an exact fill) and stratified poorly (dev got 4 + 4 of two tactics) | §D.2, T1.6 |
| 2026-10-08 | Split result for review: every split covers the same 7 tactics (dev 10, e2e 12, test 40); `collection` (1 window) is in no split (its quota rounds to 0); all 5 de-duplication pairs are kept together; the LSASS demo window (SDWIN-201018225619) is `unused`, so the demo fixture does not reuse a research window | `make splits` output | §D.2, §E.5 |
| 2026-10-08 | TanStack Table pinned to 8.x (8.21.3) | 9.x changed the API (`createCoreRowModel` …); plan names the library without a version | §C.3, §G |
| 2026-10-08 | Playwright specs live in `frontend/tests/e2e/` (plan §G: `tests/e2e/`); `make e2e`; CI job `e2e` installs Chromium and runs them | Specs must resolve `@playwright/test` from `frontend/node_modules` | §G, §I.3 |
| 2026-10-08 | e2e data: `scripts/e2e_fixture.py` builds a throw-away `app.db` + the mini-window database in `data/e2e`; the API serves the built SPA on port 8010 against it | CI-safe J1 without OTRF data or a GPU | §I.3 |
| 2026-10-08 | Table cells longer than 160 characters are shortened **for display only**, with the full length shown; the record drawer shows every value in full | Readable P1 tables. Not evidence: the verifier's evidence is never truncated (§D.7.1) | §E.1 |
| 2026-10-08 | Playwright browsers were already installed (`~/.cache/ms-playwright`, Chromium 1243 for Playwright 1.63); nothing was downloaded | — | T1.7 |
| 2026-10-08 | **Split accepted (T1.6).** `collection` has a single eligible window (SDWIN-200609225055, `unused`) and is in no split; **to be listed in the final report's limitations** | Team decision; `docs/splits.md` | T1.6, §D.2 |
| 2026-10-08 | **Eligibility reading A accepted**: primary host = most process_create + process_access events; the process event must be on that host itself (`dedup.eligibility` counts only rows whose `host` is the primary host; tested with a window whose busiest host has no process events). Each window's primary host and its share of all events are stored in `splits.json` and `docs/splits.md`; 16 of 99 windows have a share below 50% (lowest 7.7%, SDWIN-190518200432). Plan §D.2 step 1 updated | Team decision; proposal §10 asks only for host-level evidence | §D.2 |
| 2026-10-08 | **Data issue: SDWIN-230718150800 excluded; 99 of the 100 windows were ingested.** Its Host file is absent at the pinned commit; the similarly named file is not substituted | Team decision | §D.1, FR-01/02 |
| 2026-10-08 | **De-duplication accepted as is.** Shared lab-session background events push different attacks over J = 0.5; grouping only keeps each pair in the same split (cautious) | Team decision | §D.2 step 3 |
| 2026-10-08 | Acting users for `disable_account` cases (dev + test, on the primary host): 48 of 50 windows have ≥1 record naming an acting user; 45 name a non-built-in account. No acting user: SDWIN-190625133822 (dev), SDWIN-190518182022 (test). Only built-ins (system, machine accounts …): SDWIN-190319020729, SDWIN-190518221344, SDWIN-200806015757 (all test) | `docs/splits.md`; `gbya/data/window_stats.py` | §D.6.2 C3, T4.x |
| 2026-10-08 | Trusted context, beyond §F.3's rules: unknown fields rejected; `type` ∈ {human, service} and `privilege` ∈ {standard, admin, domain_admin} (the values the policy rules match); unique hosts, accounts and ticket ids; ticket times must carry a time zone | Unambiguous lookups; rule typos cannot silently miss | §F.3 |
| 2026-10-08 | Host lookup is case-insensitive; account lookup strips `DOMAIN\\` and lower-cases (same normalisation as log users) | Windows names are case-insensitive; logs store normalised users | §F.3, §D.6.2 C1 |
| 2026-10-08 | `get_context` sections: assets, identities, network, approval_script, change_tickets. The approval script is visible to the agent | Proposal §8 lists the approval script among the facts `get_context` provides. **Flag for the team** | §D.4, §D.5 |
| 2026-10-08 | Policy engine: conditions on `host.*` (asset), `account.*` (identity) and `action.*` (per-tool facts in the rules file's `tools` section, e.g. `reversible`); scalar = equality (strings case-insensitive), list = membership, `nonempty`; first match in file order; else `default`. Rule files are validated on load (tool named, state-changing tools only, known attributes, unique ids) | §D.8 names the predicate kinds; proposal §8 lists reversibility among policy attributes, so the engine can express it | §D.8, FR-07 |
| 2026-10-08 | Draft rules leave two points for the team: (1) **reversibility** is declared per tool (`kill_process` irreversible) but no rule uses it; (2) a **standard service account without dependents** matches no rule, so `disable_account` on it is `forbidden` by default | Coverage check of P1–P8 against all identity types | §D.8 |
| 2026-10-08 | Ruff line-length (E501) not enforced under `tests/` | Hand-aligned test tables | — |
| 2026-10-08 | `tokenizers` 0.23.2 is a main dependency; token counts use the model's `tokenizer.json` (from the default profile's `model_path`). An approximate counter exists for tests/CI only and is chosen automatically only when `GBYA_ENV=test`; otherwise a missing tokenizer is an error | §D.10.3 "token counts use the model's own tokenizer" | §D.5.1, §D.10.3 |
| 2026-10-08 | Provenance via sqlglot `qualify` (stars expanded, columns resolved to their source alias): a projection registers only if it is a bare `record_id` column whose source is a whitelisted base table (not a CTE or subquery); queries with GROUP BY / DISTINCT / HAVING / a non-window aggregate in the projections register nothing; UNION registers a position only if direct in every branch; INTERSECT/EXCEPT and any shape mismatch register nothing; candidates confirmed in `raw_events` | §D.5.2 table | §D.5.2, FR-10 |
| 2026-10-08 | Query rendering: JSON per row, `record_id` first, duplicate column names suffixed `#2`; fields > 200 chars shortened with the full length noted; rows that do not fit 1,500 tokens are dropped and reported in a note **outside** the untrusted block | §D.5.1 | §D.5.1 |
| 2026-10-08 | Escalation notes are flagged when any 30-character run occurs verbatim in untrusted text shown earlier in the episode | §D.5 "flagged when it contains a verbatim log substring of 30 or more characters" | §D.5, A-5 |
| 2026-10-08 | Mock actions go through a `Recorder` (in-memory now); the database recorder writing `tool_calls` arrives with the episode loop (T5.2) | No episodes exist before M5 | §D.5.2a |
| 2026-10-08 | Typed identifiers are part of the argument schemas (a bad value fails as `C1_SCHEMA`): hostname and account patterns from T2.4; PID a strict positive integer (`"4100"` and `true` rejected); IP parsed and stored in canonical form; SHA-256 64 hex. Accounts therefore never carry `DOMAIN\\` | T2.4 instructions | §D.6.2 C1 |
| 2026-10-08 | C1 order: allow-list → schema → host/account in trusted context → internal IP → protected IP → provenance of PID, IP, hash. `request_approval` gets the same checks on its embedded action; `ask_analyst`, `draft_report` and read-only tools get the schema check only; G0 uses `check_schema` only (A-7) | §D.6.2, §D.6.2a; T2.5 adds C2–C6 and the orchestrator | §D.6.2 |
| 2026-10-08 | C1's provenance accepts a PID in any typed PID field of a retrieved record (incl. `process_access.target_pid`); whether it has the actor role is C3's check (T2.5) | §D.5.2 "This is the C1 existence check" | §D.5.2, §D.6.2 |
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
| 2026-10-08 | T1.2 | `pytest tests/unit/test_catalogue.py tests/integration/test_catalogue_real.py` | 11 passed (3 fixture YAMLs: sub-technique join, network files ignored, multi-mapping, missing Host file, id check; idempotent upsert keeps later fields; real data: 100 windows, stable checksum, LSASS window T1003.001, only SDWIN-230718150800 missing) |
| 2026-10-08 | T1.2 | `make catalogue` (twice) | 100 windows; inserted 100 then updated 100; checksum `da6d27b076b40d2ad15c6cd14549c97b2ac0fbd7d4de70f87d3770462851c3ae` both times |
| 2026-10-08 | T1.3 | `pytest tests/unit/test_normalise.py tests/integration/test_normalise_real.py` | 30 passed: golden test on the hand-crafted `mini_window` fixture (25 events, 2 bad lines; every mapped event type and all 4 formats; record-ID order; PID sources; users; channels; raw JSON; `_meta`; rebuild identical; zip and tar.gz with `__MACOSX` debris; parsers); LSASS smoke test: 118 events = 95 Sysmon + 23 Security, `process_access` 48 rows, Dumpert PID 6772 in 4688 and Sysmon 1 |
| 2026-10-08 | T1.3 | `make normalise` | 99 ingested, 1 missing_host_file; 757,375 events; 0 skipped; 231 PIDs from Message; 35 s; 2.3 GB |
| 2026-10-08 | T1.3 | `make lint`, `pytest` | lint clean (mypy: 40 files); 76 passed, 1 gpu deselected |
| 2026-10-08 | T1.3a | `pytest tests/unit/test_sql_guard.py` | 70 passed: 9 accepted selects; 38 layer-1 negatives (DML/DDL, multiple statements, read_csv/read_csv_auto/'file' FROM, glob, ATTACH, PRAGMA, COPY, INSTALL, LOAD, SET, DML in CTEs, unknown/qualified/quoted tables, denylisted functions, INTERSECT, DESCRIBE, SHOW, CALL, VALUES, SELECT INTO, garbage, empty); comment cannot escape the wrapper; layer 2 with layer 1 bypassed blocks read_csv_auto, ATTACH, COPY … TO, INSTALL, SET enable_external_access, SET lock_configuration, SET memory_limit, LOAD, CREATE, INSERT (errors: file system operations disabled / configuration locked / database read-only); plain read-only connection still reads a CSV (reproduces the plan's [V]); settings applied; DuckDB 1.5.6; LIMIT 50; 0.5 s timeout interrupts a 3-way cross join and the connection stays usable; write through `open_case_db` fails; built file 0444; `duckdb.connect` only in the two factories; 13 execution-path modules imported in fresh interpreters never load `build_db` (the check reports `True` for `gbya.data.normalise`) |
| 2026-10-08 | T1.4 | `pytest tests/integration/test_windows_api.py` | 23 passed: listing and filters (split, tactic by name or ID, text, technique); unknown window 404; table rows, pagination, filters, injection attempt treated as text, bad filter 400, `_meta` not browsable, limit > 500 → 422 envelope, not-ingested window 404; record detail (normalised + raw, raw-only, missing); guarded query OK; DROP, read_csv_auto, multi-statement and `_meta` rejected with `SQL_REJECTED`; DROP had no effect |
| 2026-10-08 | T1.4 | uvicorn on the real app.db + curl | `/windows` lists 100 (99 ingested, 21 credential access); `credential_access` + "lsass" → 3 windows; J1 query on SDWIN-201018225619 returns 20 rows; DROP → SQL_REJECTED envelope |
| 2026-10-08 | T1.5 | `pytest tests/unit/test_dedup.py` | 15 passed: synthetic boundary (J 0.6 grouped, 0.4 not); J = 0.5 not grouped (strict); union-find transitive with smallest-id group; hypothesis property (150 examples): groups are exactly the connected components; normalisation rules; eligibility (< 30 events; no process events on the primary host); deterministic signatures |
| 2026-10-08 | T1.5 | `make dedup` | 99 windows, 94 groups, 5 pairs (J 0.508–0.608), 99 eligible, 37 single-host; 3 s |
| 2026-10-08 | T1.6 | `pytest tests/unit/test_split.py` | 9 passed: exact 10/40/12 + unused = replacement queue; deterministic for a seed (byte-identical JSON) and seed-sensitive; input order irrelevant; groups together; ineligible never assigned; < 62 windows → Q-2 error; every tactic in every split with per-tactic counts within 1 of the proportional share; JSON document; hypothesis property (60 examples: 62–110 windows, up to 25 pairs, random seeds): no group straddles splits and counts are exact |
| 2026-10-08 | T1.6 | `make splits` (twice) | dev 10, test 40, e2e 12, unused 37; SHA-256 `1a355e659cc204e136b39d018dc258f8ea7666b1f4e481fbe231849df2598936` both times; 2 s |
| 2026-10-08 | T1.7 | `pnpm test` (vitest) | 8 passed: shell (4) + Windows list with tactic filter sent to the API, detail with tabs and record drawer (raw JSON), SQL console success and `SQL_REJECTED` envelope with hint, unknown window error |
| 2026-10-08 | T1.7 | `make e2e` (Playwright, Chromium) | 6 passed = 3 journeys × 2 viewports (1280×800, 768×1024): J1 (Windows → credential_access → window → `SELECT * FROM process_access LIMIT 20` → open record), rejected query shows the typed error, table filter + keyboard tab navigation; **axe: 0 serious/critical violations** on the list, detail and open drawer |
| 2026-10-08 | T1.7 | Headless browser on the real app.db (port 8011) | LSASS window detail shows 48 `process_access` rows; query for lsass.exe targets → 6 rows; record 86 drawer: Security 4663, Outflank-Dumpert.exe, `source_pid` 6772 from raw `ProcessId` `0x1a74` |
| 2026-10-08 | M1 | `make lint`, `make test`, `make e2e` | lint clean (ruff, mypy: 46 files, eslint, prettier, tsc); pytest 195 passed (1 `gpu` deselected); vitest 8 passed; Playwright 6 passed |
| 2026-10-08 | T1.6 | `pytest tests/unit/test_window_stats.py test_dedup.py test_split.py` | 37 passed: acting users on the mini window (11 records on the workstation, logon 4648 → subject_user; DC: 4624/4625 → target_user), built-in filter, primary host chosen by process activity with the share over all raw events (incl. old-Winlogbeat `computer_name`), docs renderer (pairs shown on both members, pipes escaped) |
| 2026-10-08 | T1.6 | `make splits` + comparison with the reviewed file | splits, assignment, groups and replacement order identical; new SHA-256 `1894e9d9…` (host fields added) |
| 2026-10-08 | T2.1 | `pytest tests/unit/test_context.py` | 24 passed: plan example valid; one rejection per rule (tier out of range or missing, approval mode, ticket start ≥ end, bad regex, ticket host not in assets, naive ticket time, missing field, identity type/privilege, bad CIDR/IP, schema version, empty assets, unknown field); duplicate host/account/ticket; hash stable under key order and whitespace, changes with content; `get_context` sections; network and ticket helpers; immutability |
| 2026-10-08 | T2.2 | `pytest tests/unit/test_policy.py` | 27 passed: every rule P1–P8 of the draft `rules.yaml` and the default (incl. unknown host/account and the dependent-less service account), first-match order, `action.*` + `nonempty`, 7 rule-file validation errors, C5 refuses non-state-changing tools, evidence requirements cover exactly the 4 tools |
| 2026-10-08 | T2.3 | `pytest tests/unit/test_tools.py` | 46 passed on the mini window: 11 projections that register nothing (literal, expression, cast/coalesce, count, max, DISTINCT, GROUP BY, CTE, derived table, UNION with a literal branch); 12 that register exactly the expected ids (direct, `*`, `FROM`-only, alias, extra literal column, WHERE, window function, IN-subquery, UNION ALL, join of two tables, join with one direct column, LIMIT); accumulation; only rows shown are registered under the token cap; shortened-field rows registered; ids absent from `raw_events` not registered; shape mismatch safe; wrapping, `record_id` first, duplicate keys; rejected SQL; real tokenizer; 9 tools by class; no free text on state-changing tools; 6 argument errors; unknown/delete counting; trusted `get_context`; note flagging; report never sent; mock action recorded |
| 2026-10-08 | T2.3 | Mutation check | Registry replaced by "every column is a direct record_id": 14 tests fail (all register-nothing cases among them); restored, 46 pass |
| 2026-10-08 | T2.4 | `pytest tests/unit/test_c1_typed_args.py` | 31 passed: `SELECT record_id, 99999 AS pid …` then `kill_process(H, 99999)` → `C1_UNPROVENANCED_VALUE`; after `SELECT 5 AS record_id` nothing is usable; PID from a registered record passes (incl. target PID); PID of an unretrieved record fails; planted log instruction cannot select a tool; IP rules (unprovenanced, provenanced dst and src, internal, protected); 19 target/type cases; allow-list; `request_approval` embedded action checked (OK, fake PID, unknown host, schema); schema-only for other tools and G0; `CheckResult` shape; SHA-256 and IPv6 canonical provenance |
| 2026-10-08 | T2.4 | Mutation check | Provenance replaced by "always yes": 6 tests fail (incl. the 99999 fixture); restored, 31 pass |
| 2026-10-08 | M2 so far | `make lint`, `make test` | lint clean (mypy: 62 files); pytest 336 passed (1 gpu deselected); vitest 8 passed |

### T0.3 pilot (synthetic prompts; details in `docs/pilot_report.md`)

| Metric | Value |
|---|---|
| Profile | `vllm-awq`: vLLM 0.31.0, Qwen2.5-7B-Instruct-AWQ @ `b2503754`, ctx 8192, util 0.90, **FP16 KV** (21,520 tokens ≈ 2.6 × 8,192-token requests), 4 seqs |
| Input / output throughput (20 episodes, 4 concurrent) | 1,229.2 / 55.4 tok/s (assumed ≥ 800 / ≥ 80) |
| Measured wall vs budget formula `in/800 + out/80` | 288.7 s vs 643.6 s (0.45×) |
| Schema-valid | 78/80 calls (20 episodes); 459/480 in the soak; 73/80 with repetition_penalty 1.05 |
| Peak VRAM / host RAM | 7,249 MiB / 8,201 MB |
| 30-min soak | 30.7 min; input 1,161 tok/s mean; output 56.0 tok/s mean; max 80 °C; no thermal throttling flag |
