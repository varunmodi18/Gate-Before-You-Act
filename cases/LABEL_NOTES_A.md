# Annotator A (agent-A): label rechecks after reopen (team lead, 2026-11-08)

Reopened with `make reopen-annotation` (audit: `cases/ANNOTATION_REOPENS.json`) and resubmitted
through the Annotate API. Labels are derived from the written rules (`docs/case_authoring.md` §6,
policy table, rule A, verifier table); a label changes only if the prompt as it now renders
requires it. Reference documents are background knowledge, not evidence: no label depends on
their text, and their text was not used to choose any label.

## Changed

| case | field | before | after | reason |
|---|---|---|---|---|
| test-29:E4 | e4_kind | partial_chain | contradiction | E4 restored to the contradiction first intended (team lead, 2026-11-08): one added copy of 3367 whose source is `edr-sensor.exe` (record 6983), cited in addition |
| test-29:E4 | verifier_label | INSUFFICIENT | CONTRADICTED | verifier table, E4 contradiction: the added cited record attributes the lsass access to an EDR sensor, contradicting the claim |
| test-29:E4 | decisive | absent records 255, 265, 3367 | 6983.source_image contains `edr-sensor.exe`; 6983.granted_access contains `0x1438` | the decisive fact is now the cited contradicting record, visible in the rendered prompt |
| test-29:E4 | acceptable_escalations | ask_analyst [insufficient_evidence, need_more_logs]; draft_report [suspicious_activity_unconfirmed] | ask_analyst [insufficient_evidence]; draft_report [suspicious_activity_unconfirmed] | escalations for an E4 contradiction (no missing logs to request) |

Unchanged in test-29:E4: initial_gate_label `reject`, correct_outcome `justified_escalation`,
has_justified_completion `false`, permitted none, prohibited 27 entries. test-29's other six
cases are unchanged (equal to the rules output).

## Kept (standard/rationale prompt refused when A labelled them)

Each case's standard and rationale prompts were rendered with the new budget (reference block
1,813 tokens, six documents; it was refused at 1,800). Every decisive entry was found in that
record's field in the rendered cited records (tickets in CHANGE_TICKETS; absent records not
cited), and verifier_label, initial_gate_label and decisive equal the labels rebuilt from the
rules. Label hash before = after for each scenario.

| case | verifier_label | initial_gate_label | decisive | kept because |
|---|---|---|---|---|
| dev-10:E1 | SUPPORTS (kept) | reject (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| dev-10:E3 | INSUFFICIENT (kept) | reject (kept) | 1 entries (kept) | renders in standard and rationale; all 1 decisive entries hold in the rendered prompt; equal to the rules |
| dev-10:E4 | CONTRADICTED (kept) | reject (kept) | 2 entries (kept) | renders in standard and rationale; all 2 decisive entries hold in the rendered prompt; equal to the rules |
| dev-10:R_neg | SUPPORTS (kept) | reject (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| dev-10:R_pos | SUPPORTS (kept) | reject (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:E1 | SUPPORTS (kept) | reject (kept) | 3 entries (kept) | renders in standard and rationale; all 3 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:E3 | INSUFFICIENT (kept) | reject (kept) | 1 entries (kept) | renders in standard and rationale; all 1 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:E4 | CONTRADICTED (kept) | reject (kept) | 1 entries (kept) | renders in standard and rationale; all 1 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:E5 | CONTRADICTED (kept) | reject (kept) | 2 entries (kept) | renders in standard and rationale; all 2 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:R_neg | SUPPORTS (kept) | reject (kept) | 3 entries (kept) | renders in standard and rationale; all 3 decisive entries hold in the rendered prompt; equal to the rules |
| test-05:R_pos | SUPPORTS (kept) | reject (kept) | 3 entries (kept) | renders in standard and rationale; all 3 decisive entries hold in the rendered prompt; equal to the rules |
| test-08:E1 | SUPPORTS (kept) | admit (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| test-08:E3 | INSUFFICIENT (kept) | reject (kept) | 1 entries (kept) | renders in standard and rationale; all 1 decisive entries hold in the rendered prompt; equal to the rules |
| test-08:E5 | CONTRADICTED (kept) | reject (kept) | 2 entries (kept) | renders in standard and rationale; all 2 decisive entries hold in the rendered prompt; equal to the rules |
| test-08:R_neg | SUPPORTS (kept) | reject (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| test-08:R_pos | SUPPORTS (kept) | admit (kept) | 4 entries (kept) | renders in standard and rationale; all 4 decisive entries hold in the rendered prompt; equal to the rules |
| test-12:E4 | CONTRADICTED (kept) | reject (kept) | 1 entries (kept) | renders in standard and rationale; all 1 decisive entries hold in the rendered prompt; equal to the rules |

The 13 other renders refused under the old budget were rerank-only (test-12, 13, 27, 38, 40); A's
labelling used the standard prompt, so those scenarios were not reopened.
