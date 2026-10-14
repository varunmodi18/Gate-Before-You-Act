# Gate Before You Act

GateBench: a research workbench for evidence-gated LLM security-agent experiments
(Team Simpletons). An LLM agent investigates a Windows host from its logs and proposes a
response; a **gate** checks every state-changing action — policy rules alone, or also whether
the cited log evidence supports the action — and the experiments measure which part helps.

- Plan (authoritative): [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) · progress and decisions:
  [STATUS.md](STATUS.md) · proposal: [docs/proposal/](docs/proposal/)
- **Team members start with [docs/TEAM_GUIDE.md](docs/TEAM_GUIDE.md)** (shared server, roles,
  assignments, dated checklist).

## Quickstart

Needs Linux, Python 3.11 via [uv](https://docs.astral.sh/uv/), Node 22 via nvm and pnpm.

**A. Quick look (about 5 minutes; no OTRF data, no GPU, no model).** Fixture data and the Fake
model (a scripted test profile, labelled FAKE in the header):

```bash
make setup
make fixture-up        # builds data/quick, serves http://127.0.0.1:8000 (Ctrl-C stops it)
```

**B. The demo (real OTRF window; about 15 minutes plus downloads).**

```bash
make setup
make db data index     # OTRF at the pinned commit, SigmaHQ + ATT&CK index (~0.5 GB)
make demo-setup        # the demo-lsass scenario and the demo windows in data/demo
make model-up          # live model (RTX 4060 8 GB, vLLM AWQ; see docs/pilot_report.md)
                       # weights (~5.5 GB) once: scripts/fetch_model.py, pinned revision in
                       # config/model_profiles.yaml
make demo-up           # in a second terminal; open http://127.0.0.1:8000
```

Without the model, `make demo-replay` serves the demo from the recorded cassette
(`data/fixtures/demo/replay/demo.jsonl`, once recorded with `make demo-record` and
`scripts/record_replay.py`); the header then shows **REPLAY** and runs are never research.

**C. The research instance.** `make up` (API and worker on `127.0.0.1:8000`, SPA included);
the team reaches it through an SSH tunnel ([docs/TEAM_GUIDE.md](docs/TEAM_GUIDE.md) §2).
Operations: [docs/runbook.md](docs/runbook.md).

## Demo script (plan §E.5, about 10 minutes)

| # | Do | Expect |
|---|---|---|
| 1 | Home | health cards green; the model card shows backend, model ID and last pilot tok/s |
| 2 | Windows → the LSASS window → `process_access` | records targeting `lsass.exe`, with record IDs |
| 3 | Scenarios → `demo-lsass` | variant grid, identical context hash E1–E5, validator all-pass |
| 4 | Gate Playground → E1 → G1, A1, G3 | all admit; G3's verifier `SUPPORTS` with its reason |
| 5 | Playground → E3 → G1, G2, A1, G3 | G1, G2 admit; A1 and G3 reject at C3 `C3_HOST_MISMATCH` |
| 6 | Playground → E5 → G1, A1, G3 | G1, A1 admit; G3 rejects at C4 `CONTRADICTED` (approved ticket) |
| 7 | Agent Console → E2 with G3 | `INSUFFICIENT`, a recovery query, re-cited, outcome shown |
| 8 | Console → R_neg with G3 | C6 converts to `request_approval`, `NO_RESPONSE`, the agent escalates |
| 9 | Experiments → new Exp 1 on the demo cases | completes in minutes, progress visible |
| 10 | Results for that run | illustrative mode (watermark): estimates and intervals, no verdicts |

Steps 4, 6 and 7 depend on the live model; the demo shows what the run produces.

## Commands

`make help` lists every target. The common ones: `make test` (pytest without GPU + vitest),
`make lint`, `make e2e` (Playwright journeys at 1280 and 768 px with axe, then the replay
demo), `make docs` (regenerates `docs/api.md` and `docs/gate_contract.md`), `make diag`.

## Troubleshooting

`make diag` prints versions, GPU memory, free disk, database counts, the frozen case set and the
model server's state — include it in any bug report. Logs: `logs/api.log`, `logs/worker.log`,
`logs/model.log`.

| Symptom | Likely cause | Action |
|---|---|---|
| Model card red, `MODEL_UNAVAILABLE` | model server not started or crashed | `make model-up`; read `logs/model.log` |
| CUDA out of memory on load | utilisation or context too high; display on the dGPU | lower `max-model-len`; display on the iGPU; profile `llamacpp-q4` |
| Very slow tokens/s | thermal throttling, battery mode | plug in AC, performance profile; see the pilot report |
| A run is stuck at an item | worker not running | `make worker` (it resumes) |
| Analysis returns 422 | case set not frozen, replay run, or adjudication pending | freeze, use live research runs, clear the queue |
| `REPLAY_MISS` | the replayed demo left the recorded script | use the live model, or re-record |
| Teammates cannot open the UI | tunnel not running, laptop asleep, API not on 127.0.0.1:8000 | `docs/TEAM_GUIDE.md` §2 |

## Documentation

| Document | For |
|---|---|
| [docs/TEAM_GUIDE.md](docs/TEAM_GUIDE.md) | the team: roles, shared server, assignments, checklist |
| [docs/case_authoring.md](docs/case_authoring.md) | authoring and labelling scenarios (rule A, scope, markers) |
| [docs/runbook.md](docs/runbook.md) | running the experiments and the analysis |
| [docs/gate_contract.md](docs/gate_contract.md) | the normative gate checks C1–C6 (generated from the plan) |
| [docs/api.md](docs/api.md) | every API route (generated from OpenAPI) |
| [docs/policy_table.md](docs/policy_table.md) | the signed-off policy rules |
| [docs/fieldmap.md](docs/fieldmap.md), [docs/splits.md](docs/splits.md) | log normalisation; the windows and splits |
| [docs/pilot_report.md](docs/pilot_report.md) | model serving settings and measured throughput |

## Third-party data and attribution

The retrieval index (`make index`) is built locally from the sources below at pinned versions.
Neither the rule files nor the index are committed or redistributed; each user rebuilds them
from source (`data/` is git-ignored).

**Sigma rules.** SigmaHQ, <https://github.com/SigmaHQ/sigma>, release `r2026-07-01`
(commit `552f3fee420ef232a8e5790c4fae591847e32347`), rules under `rules/windows/` only.
The rules are licensed under the Detection Rule License (DRL) 1.1,
<https://github.com/SigmaHQ/Detection-Rule-License>. The rules are indexed with their ATT&CK tags
and technique IDs removed from the indexed text. Wherever GateBench shows a retrieved rule, it
shows the rule's title, its author(s) as given in the rule's `author` field, and a link to the
rule file at the pinned commit.

**MITRE ATT&CK®.** Enterprise ATT&CK STIX 2.1 bundle, version 19.2
(`enterprise-attack-19.2.json` from <https://github.com/mitre-attack/attack-stix-data>, tag
`v19.2`). ATT&CK is used under MITRE's ATT&CK terms of use (the repository's `LICENSE.txt`):

> © 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of
> The MITRE Corporation.

ATT&CK® is a registered trademark of The MITRE Corporation.

**Security-Datasets.** Log data from OTRF Security-Datasets,
<https://github.com/OTRF/Security-Datasets>, commit `d9d40ef123d2c87d5d3df28c96bcab4f0faccc87`.
