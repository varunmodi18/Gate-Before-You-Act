# Final results — Gate Before You Act (cases-v1)

Research runs of 8 Nov 2026 on the frozen case set **cases-v1** (`case_set_hash`
`1e7379ff6c44d39c07056b235ce9ac53570ddcff8530b903c51701a56e49599a`). Every number here is copied
from `results.json`, which `scripts/final_results.py` builds from the research endpoints.
Rates are proportions; `*_pp` fields are percentage points for display.

## Runs

| Run | Experiment | Cases | Units | Completed | Errors | Wall / GPU h |
|---|---|---|---|---|---|---|
| 1 | Exp 1 (9 configurations, 4 verifier variants × 3) | 217 test | 6,727 | 6,727 | 0 | 0.50 |
| 2 | Exp 2 (G0, G1, G3 × 3; G2, A1, A5, A7, A8 × 1; no F1) | 217 test | 3,038 | 3,038 | 0 | 1.44 |
| 3 | Exp 3 (G0, G1, G3 × 2) | 12 e2e | 72 | 72 | 0 | 0.19 |

All three runs share the same setup:
- **Model:** Qwen2.5-7B-Instruct-AWQ @ b250375, `model_file_sha256` bd4972a9…
- **Serving:** vLLM 0.31.0, profile `vllm-awq` (FP16 KV cache, xgrammar).
- **Code:** git 4e6d50b.

Full provenance for each run is in `runs.csv` and `results.json`.

## Hypotheses

Cluster bootstrap over 31 scenarios, B = 10,000, seed 2026, percentile 95% CI. Values below are exactly as `POST /analysis/hypotheses` returns them.

| | Estimate | 95% CI | Cases | Threshold | Result |
|---|---|---|---|---|---|
| **H1** wrongly admitted E2–E5, G1 − G3 | 0.290 | [0.201, 0.387] | 124 | target 0.15 | target_reached true, statistically_supported true |
| **H1-C4** wrongly admitted E4–E5, A1 − G3 | 0.161 | [0.065, 0.274] | 62 | target 0.10 | target_reached true, statistically_supported true |
| **H2** unnecessary deferral on justified cases, G3 − G1 | 0.355 | [0.257, 0.459] | 98 | margin 0.10 | decision **harm_beyond_margin** |

**H2 depends on the run adjudication.** 120 unlisted Exp 2 items were decided by one agent session under `docs/case_authoring.md` §6 (`cases/RUN_ADJUDICATION_LOG.md`). 115 of them were escalations judged not qualifying, mostly `ask_analyst` in cases with a justified completion.

As a sensitivity check, H2 was recomputed in memory with every adjudicated escalation counted as qualifying:
- **Result:** 0.116 [0.062, 0.179], decision `inconclusive` (`h2_adjudication_sensitivity.csv`).
- **What changed:** 155 G3 episodes and 61 G1 episodes moved from deferral or refusal to justified escalation.
- **What does not change:** G3 defers more than G1 either way.

The registered result is the first one. The sensitivity check is exploratory.

## Other findings to report (exploratory unless marked)

- **The verifier blocks good packages too (Exp 1).** G3 wrongly blocks 0.350 of admit-labelled packages (E1, R_pos; 120 decisions). G1 and G2 block 0.000.
- **Wrongly admitted on E2–E5**, decisions pooled over runs (`exp1_by_system_variant_objective.csv`, figure `exp1_wrongly_admitted.png`):

  | System | Wrongly admitted |
  |---|---|
  | G0 | 1.000 |
  | G1 | 0.573 |
  | G2 | 0.573 |
  | A1 | 0.387 |
  | A3 | 0.331 |
  | A6 | 0.306 |
  | G3 | 0.282 |
  | A2 | 0.282 |
  | A4 | 0.282 |

  G1 = G2 and A2 = G3 exactly.
- **By objective** (`breakdowns_exploratory.csv`):
  - `disable_account` contributes 0 to H1 and H1-C4 for both systems, because P5 requires approval on every one of these cases.
  - H1 is 0.325 for `contain_host` and 0.575 for `stop_process`.
  - H1-C4 is 0.000 for `contain_host`: A1 and G3 both admit every E4/E5 `contain_host` package. It is 0.500 for `stop_process`.
- **Verifier exact accuracy, all packages** (diagnostic; 0 parse errors; the 3 runs are identical at temperature 0):

  | Variant | Exact accuracy |
  |---|---|
  | standard | 0.576 |
  | rationale | 0.636 |
  | none | 0.535 |
  | rerank | 0.567 |

  On E5 the exact accuracy is 0.000 in every variant: no CONTRADICTED verdict was ever produced.
- **Retrieval on test** (189 cases with a non-empty gold set; mean Recall@5 ceiling 0.409):

  | Mode | Recall@5 | Hit@5 | nDCG@5 | MRR@20 |
  |---|---|---|---|---|
  | BM25 | 0.0039 | 0.063 | 0.015 | 0.068 |
  | Reranker | 0.0073 | 0.132 | 0.028 | 0.096 |

- **RAG ablation** (Exp 1, wrongly admitted E2–E5): A4 − G3 = 0.000 [−0.056, 0.056]; A6 − G3 = 0.024 [0.000, 0.056].
- **Recovery ablation and MCP** (Exp 2, matched run 1): A5, A7 and A8 differ from G3 by at most 0.005 on every outcome. The recovery budget almost never applies: G3 had 5 E2 recovery opportunities in all runs and recovered 0. A8 (MCP transport) matches G3.
- **Safety** (`safety.csv`, figure `exp2_unsafe_proposed_admitted.png`):

  Exp 2, unsafe calls admitted by the gate:

  | System | Admitted / proposed | Share |
  |---|---|---|
  | G0 | 467 / 467 | 1.000 |
  | G1 | 270 / 570 | 0.474 |
  | G2 | 81 / 257 | 0.315 |
  | A1 | 60 / 268 | 0.224 |
  | G3 | 150 / 929 | 0.161 |

  - **Injection:** one G3 episode proposed the injected call (1 of 294 G3 episodes on injected cases); the gate admitted 0. No other system proposed it.
  - **Log deletion:** 0 attempts.
  - **Exp 3:** G1 and G3 admitted 0 of 4 unsafe proposals each; G0 admitted 4 of 4.
- **Exp 3** (24 episodes per system):

  | System | Task success | Technique F1 | Cited-evidence precision | Cited-evidence recall |
  |---|---|---|---|---|
  | G0 | 0.750 | 0.103 | 0.695 | 0.200 |
  | G1 | 0.875 | 0.105 | 0.719 | 0.192 |
  | G3 | 0.583 | 0.100 | 0.744 | 0.129 |

  Every system found 2 of the 24 gold techniques. None of the 12 predicted techniques outside the window metadata was judged incidental.
- **Error taxonomy:** no tags are applied yet; the six tags are applied by people on the Traces page. `taxonomy_suggested.csv` gives the three tags the code suggests. Over-deferral is suggested for G3 in 151 of 651 episodes, against 46 for G1. `example_traces.csv` lists 10 traces chosen by a fixed rule, not by a person.

## Files

| File | What it holds |
|---|---|
| `results.json` | Everything below, plus the raw endpoint responses and provenance |
| `runs.csv` | Run provenance |
| `hypotheses.csv` | H1, H1-C4, H2 |
| `h2_adjudication_sensitivity.csv` | The H2 sensitivity check |
| `breakdowns_exploratory.csv` | Hypotheses by variant and by objective |
| `exp1_by_system_variant_objective.csv` | Exp 1 by system, variant and objective |
| `exp1_verifier_accuracy.csv` | Verifier accuracy |
| `retrieval.csv` | Retrieval metrics |
| `exp2_outcomes_by_objective.csv`, `exp2_outcomes_by_variant.csv` | Exp 2 outcomes |
| `exp3_outcomes.csv`, `exp3_technique_evidence.csv` | Exp 3 |
| `ablations_exploratory.csv` | RAG, recovery and MCP ablations |
| `safety.csv` | Safety measures |
| `taxonomy_suggested.csv`, `example_traces.csv` | Error taxonomy |
| `figures/*.png` (`*.svg` sources) | Hypotheses forest plot, Exp 2 and Exp 3 outcomes, Exp 1 wrongly-admitted heatmap, unsafe proposed vs admitted |

To regenerate, with the API running on the research app.db:

```bash
uv run python scripts/final_results.py --exp1 1 --exp2 2 --exp3 3
node scripts/svg_to_png.mjs docs/final/figures
```
