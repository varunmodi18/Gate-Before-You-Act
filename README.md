# Gate Before You Act

GateBench: a research workbench for evidence-gated LLM security-agent experiments
(Team Simpletons).

- Specification: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
- Proposal: [docs/proposal/](docs/proposal/)

Setup and usage instructions will be added as the implementation progresses.

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
