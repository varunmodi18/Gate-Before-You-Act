# API reference

Generated from the OpenAPI schema by `scripts/gen_docs.py` (`make docs`); do not edit.
Every path is under `/api/v1`. Errors use `{"error": {"code", "message", "hint", "details"}}` (plan §F.6). Proportions are authoritative; `*_pp` fields are display only.

## adjudications

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/adjudications` | Queue | `run_id`? |  |
| POST | `/adjudications` | Decide |  | `Decision` |

## analysis

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| POST | `/analysis/code-ticket` | Code Ticket |  | `RunBody` |
| POST | `/analysis/compare` | Compare |  | `CompareBody` |
| POST | `/analysis/hypotheses` | Hypotheses |  | `HypothesesBody` |
| POST | `/analysis/illustrative` | Illustrative |  | `RunBody` |
| POST | `/analysis/sensitivity` | Preliminary sensitivity (T7.3): pilot discordance and correlation, and the expected half-widths at the full design size. Shown as preliminary sensitivity, never as power. |  | `SensitivityBody` |

## annotate

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/annotate` | Scenarios with generated cases and where each annotator stands (no labels). |  |  |
| GET | `/annotate/agreement` | Pooled κ (outcome; candidate actions) and E1 evidence Jaccard over the stored snapshots. | `split`? |  |
| GET | `/annotate/{sid}` | Workspace | `sid`, `role` |  |
| POST | `/annotate/{sid}/adjudicate` | Final labels for every case: recorded in ``adjudications``, written back to the case files and app.db, then the scenario is validated. Only after both annotators submitted. | `sid` | `AdjudicationBody` |
| POST | `/annotate/{sid}/cases/{variant}/decisive-check` | Do these decisive entries hold in the rendered prompt (validator check h)? | `sid`, `variant` | `DecisiveCheck` |
| POST | `/annotate/{sid}/cases/{variant}/query` | Guarded SQL on the case's own database (evidence picker); for set X, the window's. | `sid`, `variant` | `QueryRequest` |
| GET | `/annotate/{sid}/compare` | Both annotations and their disagreements — only after both have submitted. | `sid` |  |
| POST | `/annotate/{sid}/context-check` | Q-X4: a second team member confirms an end-to-end window's trusted context. Refused for the context's own author, without a recorded author, or once frozen. | `sid` | `ContextCheck` |
| POST | `/annotate/{sid}/e2-flag` | Mark one of E2's sampled records as attack-related: it is excluded from sampling, E2 is regenerated (that record is replaced by the next seeded candidate) and the replacement is logged in ``cases/<sid>/e2_flags.json``. Both annotators see the log on the E2 case. | `sid` | `E2Flag` |
| PUT | `/annotate/{sid}/{role}` | Save | `sid`, `role` | `AnnotationBody` |
| POST | `/annotate/{sid}/{role}/submit` | Submit | `sid`, `role` |  |

## episodes

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/episodes` | List Episodes | `run_id`?, `case_id`?, `limit`? |  |
| POST | `/episodes` | Start |  | `EpisodeStart` |
| GET | `/episodes/cases` | Cases the Console can run: those with a generated prefix (``mode: case``) and the end-to-end cases, whose window is investigated live (``mode: window``). |  |  |
| GET | `/episodes/schedule` | Schedule |  |  |
| GET | `/episodes/systems` | Every gate configuration, with why it cannot run here (None = it can). |  |  |
| GET | `/episodes/{episode_id}` | Get Episode | `episode_id` |  |
| POST | `/episodes/{episode_id}/approval` | Approve | `episode_id` | `ApprovalAnswer` |
| POST | `/episodes/{episode_id}/stop` | Stop | `episode_id` |  |
| GET | `/episodes/{episode_id}/stream` | Server-sent events for the episode's steps, from the first one, until it ends. | `episode_id` |  |
| PUT | `/episodes/{episode_id}/tags` | Put Tags | `episode_id` | `Tags` |

## health

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/health` | Health |  |  |

## playground

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/playground/cases` | Cases |  |  |
| POST | `/playground/gate` | Run Gate |  | `GateRequest` |
| GET | `/playground/systems` | Systems |  |  |

## runs

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/runs` | List Runs |  |  |
| POST | `/runs` | Create |  | `RunCreate` |
| GET | `/runs/exp1-spec` | The default Exp 1 configuration (``experiments/exp1.yaml``), the verifier variant of each composed system and which variants cannot run here, for the run-creation form. |  |  |
| GET | `/runs/exp2-spec` | The default Exp 2 configuration (``experiments/exp2.yaml``) and which of its systems are not defined yet (A7 with T5.7, A8 with T5.8). |  |  |
| GET | `/runs/{run_id}` | Get Run | `run_id` |  |
| POST | `/runs/{run_id}/cancel` | Cancel | `run_id` |  |
| GET | `/runs/{run_id}/items` | Items | `run_id`, `status`?, `limit`? |  |
| GET | `/runs/{run_id}/outcomes` | Exp 2 outcome counts per system; ``unscored`` while a case's labels are pending. | `run_id` |  |
| GET | `/runs/{run_id}/progress` | Server-sent events: one ``progress`` event per second until the run is terminal. | `run_id` |  |
| GET | `/runs/{run_id}/results` | Every FR-21 metric per system (T7.1; ``scoring/results.py``), with denominators; a metric whose labels or adjudications are pending is refused, not estimated. | `run_id` |  |
| POST | `/runs/{run_id}/resume` | Queue the run again; failed units get a fresh set of attempts. | `run_id` |  |
| GET | `/runs/{run_id}/retrieval` | Retrieval metrics by mode and case variant, from the stored rankings of the run's cases (§D.3): Recall@5, Hit@5, nDCG@5, MRR@20, ATT&CK top-1, mean \|G\|, exclusions. | `run_id` |  |
| POST | `/runs/{run_id}/start` | Start | `run_id` |  |
| GET | `/runs/{run_id}/verifier-eval` | Exp 1V diagnostic accuracy by verifier variant and case variant (with denominators), and C4 invocation counts per gate configuration (§D.7.2). | `run_id` |  |

## scenarios

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/scenarios` | List Scenarios |  |  |
| POST | `/scenarios` | Create |  | `ScenarioCreate` |
| GET | `/scenarios/{sid}` | Get Scenario | `sid` |  |
| PUT | `/scenarios/{sid}` | Validate against the scenario schema and write ``scenario.json`` (frozen: refused). | `sid` | JSON |
| GET | `/scenarios/{sid}/cases/{variant}` | The case file, its prefix and, for E3-E5, the database diff against the window. | `sid`, `variant` |  |
| POST | `/scenarios/{sid}/generate` | Build the variants through the construction-path command line, as a short-lived subprocess (§D.1.1): the API never patches a database itself. | `sid` |  |
| POST | `/scenarios/{sid}/scope` | The scope computed from the scenario window's records for the given hosts (decision A): the Studio's "Fill from the window" button; the author reviews it before saving. | `sid` | `ScopeRequest` |
| POST | `/scenarios/{sid}/validate` | Checks a-i and Set R (§D.11); label checks are ``pending`` until annotation. | `sid` |  |

## windows

| Method | Path | Summary | Parameters | Body |
|---|---|---|---|---|
| GET | `/windows` | List Windows | `split`?, `tactic`?, `q`? |  |
| GET | `/windows/{window_id}` | Get Window | `window_id` |  |
| POST | `/windows/{window_id}/query` | Guarded Query | `window_id` | `QueryRequest` |
| GET | `/windows/{window_id}/records/{record_id}` | Record Detail | `window_id`, `record_id` |  |
| GET | `/windows/{window_id}/tables/{table}` | Table Rows | `window_id`, `table`, `limit`?, `offset`?, `filter`? |  |
