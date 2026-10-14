// Thin JSON client for /api/v1. Errors follow the envelope of plan §F.6.

export const API_BASE = '/api/v1'

export interface ApiErrorBody {
  code: string
  message: string
  hint?: string | null
  details?: Record<string, unknown>
}

export class ApiError extends Error {
  readonly status: number
  readonly body: ApiErrorBody

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.status = status
    this.body = body
  }
}

async function parse<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    let body: ApiErrorBody = { code: 'HTTP_ERROR', message: `HTTP ${resp.status}` }
    try {
      const json = (await resp.json()) as { error?: ApiErrorBody }
      if (json.error) body = json.error
    } catch {
      // Non-JSON error body: keep the generic message.
    }
    throw new ApiError(resp.status, body)
  }
  return (await resp.json()) as T
}

type Params = Record<string, string | number | string[] | undefined | null>

export function withParams(path: string, params?: Params): string {
  if (!params) return path
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((v) => qs.append(key, v))
    else qs.append(key, String(value))
  }
  const s = qs.toString()
  return s ? `${path}?${s}` : path
}

export async function apiGet<T>(path: string, params?: Params): Promise<T> {
  const resp = await fetch(`${API_BASE}${withParams(path, params)}`, {
    headers: { Accept: 'application/json' },
  })
  return parse<T>(resp)
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parse<T>(resp)
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return parse<T>(resp)
}

// ---- Types mirroring the backend models (gbya.api.routers.*) ----

export interface Health {
  api: string
  worker?: { alive: boolean; last_heartbeat: string | null }
  model?: {
    status: string
    backend: string
    model_id: string
    last_pilot: {
      in_tps: number | null
      out_tps: number | null
      peak_vram_mb: number | null
    } | null
    recording?: string | null // demo recording into a replay cassette (T8.2)
  }
  data?: { windows: number; index: boolean }
  case_set?: { frozen: boolean; version: string | null; case_set_hash: string | null }
}

export interface WindowSummary {
  id: string
  title: string
  techniques: string[]
  tactics: string[]
  tactic_names: string[]
  hosts: string[]
  event_count: number | null
  split: string | null
  ingest_status: string | null
}

export type Cell = string | number | boolean | null

export interface TableRows {
  columns: string[]
  rows: Cell[][]
  total: number
}

export interface QueryRows {
  columns: string[]
  rows: Cell[][]
  sql: string
}

export interface RecordDetail {
  record_id: number
  table: string | null
  normalised: Record<string, Cell> | null
  raw: unknown
}

export const LOG_TABLES = [
  'process_create',
  'process_access',
  'network',
  'registry',
  'file',
  'logon',
  'share_access',
  'raw_events',
] as const

export const TACTICS: { id: string; name: string }[] = [
  { id: 'TA0043', name: 'reconnaissance' },
  { id: 'TA0042', name: 'resource_development' },
  { id: 'TA0001', name: 'initial_access' },
  { id: 'TA0002', name: 'execution' },
  { id: 'TA0003', name: 'persistence' },
  { id: 'TA0004', name: 'privilege_escalation' },
  { id: 'TA0005', name: 'defense_evasion' },
  { id: 'TA0006', name: 'credential_access' },
  { id: 'TA0007', name: 'discovery' },
  { id: 'TA0008', name: 'lateral_movement' },
  { id: 'TA0009', name: 'collection' },
  { id: 'TA0011', name: 'command_and_control' },
  { id: 'TA0010', name: 'exfiltration' },
  { id: 'TA0040', name: 'impact' },
]

// ---- Gate Playground (gbya.api.routers.playground) ----

export interface CheckResult {
  check: string
  passed: boolean
  code: string
  message: string
  details: Record<string, unknown>
  duration_ms: number
}

export interface PlaygroundCase {
  id: string
  scenario_id: string
  set: string
  variant: string
  request: { objective?: string; target?: Record<string, unknown>; text?: string }
  package: { tool: string; args: Record<string, unknown>; cited: number[] }
  expected_label: string | null
}

export interface SystemInfo {
  id: string
  checks: string[]
  capability: string
  verifier_variant: string | null
  retrieval_mode: string | null
  available: boolean
  reason: string | null
}

export interface VerifierOutput {
  verdict: 'SUPPORTS' | 'INSUFFICIENT' | 'CONTRADICTED'
  unmet_requirement: string | null
  ticket_scope: { applies: boolean; matches: Record<string, boolean> } | null
  reason: string
}

export interface ReferenceHit {
  kind: 'sigma' | 'attack'
  rank: number
  doc_id: string
  title: string
  author: string | null
  uri: string | null
  technique_id: string | null
  bm25: number
  rerank: number | null
  shown: boolean
  gold: boolean
}

export interface VerifierCallOut {
  variant: string
  output: VerifierOutput | null
  error: string | null
  raw: unknown
  prompt_hash: string | null
  manifest: Record<string, unknown>
  tokens_in: number
  tokens_out: number
  ms: number
  messages: { role: string; content: string }[] | null
  references: ReferenceHit[]
}

export interface DecisionOut {
  config_id: string
  verdict: string
  admitted: boolean
  failed_check: string | null
  checks: CheckResult[]
  verifier: Record<string, unknown> | null
  verifier_call: VerifierCallOut | null
  approval: { code: string; message: string; accepted: boolean } | null
  message: string
  ms: number
  correct: boolean | null
}

export interface GateResponse {
  case_id: string
  expected_label: string | null
  verifier_label: string | null
  decisions: DecisionOut[]
}

// ---- Experiments (gbya.api.routers.runs) ----

export interface Progress {
  run_id: number
  status: string
  done: number
  total: number
  errors: number
  running: number
  eta_s: number | null
  tps_in: number | null
  tps_out: number | null
  budget?: Budget | null
}

/** Budget monitor (T5.5): GPU-hours of one GPU, projected at the run's measured pace. */
export interface Budget {
  limit_h: number
  elapsed_h: number
  run_h: number
  spent_other_research_h: number
  projected_total_h: number
  over: boolean
  offer: {
    fallback: string
    drop_systems: string[]
    runs: Record<string, number>
    units: number
    run_h: number
    projected_total_h: number
  } | null
}

export interface Exp2SpecOut {
  spec: {
    experiment: number
    schedule: Record<string, { temperature: number; seed: number }>
    systems: Record<string, number>
    fallback: string | null
  }
  undefined: string[]
}

export interface OutcomesOut {
  run_id: number
  by_system: Record<string, Record<string, number>>
  task_success?: {
    by_system: Record<string, { rate: number | null; rate_pp: number | null; n: number }>
    refused: string | null
  }
  // Exp 2, decision B: decisive records the agent retrieved but never cited
  citation_gap?: {
    by_variant_system: {
      variant: string
      system: string
      episodes: number
      with_decisive: number
      retrieved: number
      retrieved_not_cited: number
      rate: number | null
    }[]
  }
}

export interface RunOut {
  id: number
  experiment: number
  purpose: string
  status: string
  replay: boolean
  research_eligible: boolean
  config: { spec?: Exp1Spec; case_ids?: string[] } & Record<string, unknown>
  config_hash: string
  case_set_hash: string | null
  git_sha: string | null
  model_id: string | null
  model_file_sha256: string | null
  backend: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  progress: Progress
}

export interface Exp1Spec {
  experiment: number
  verifier_variants: string[]
  verifier_runs: number
  code_only: string[]
  composed: string[]
}

export interface Exp1SpecOut {
  spec: Exp1Spec
  unavailable: Record<string, string>
  variant_of: Record<string, string>
}

export interface Accuracy {
  n: number
  exact_correct: number
  binary_correct: number
  exact_accuracy: number | null
  binary_accuracy: number | null
}

export interface VerifierEvalOut {
  run_id: number
  purpose: string
  research_eligible: boolean
  diagnostic: Record<
    string,
    Accuracy & {
      parse_errors: number
      confusion: Record<string, Record<string, number>>
      by_case_variant: Record<string, Accuracy>
    }
  >
  gate_path: Record<
    string,
    {
      packages: number
      reached_c4: number
      conditional: Accuracy
      by_case_variant: Record<string, { packages: number; reached_c4: number }>
    }
  >
  unlabelled_cases: string[]
  e2_c3?: Record<string, Record<string, { n: number; c3_rejected: number; rate: number | null }>>
}

export interface RunItem {
  case_id: string
  system: string
  run_idx: number
  status: string
  attempts: number
  error: string | null
}

export interface RetrievalSummary {
  cases: number
  sigma_n: number
  excluded_empty_gold: number
  recall_at_5: number | null
  hit_at_5: number | null
  ndcg_at_5: number | null
  mrr_at_20: number | null
  mean_gold_size: number | null
  mean_recall_ceiling: number | null
  attack_n: number
  attack_top1: number | null
}

export interface RetrievalOut {
  run_id: number
  index_sha256: string
  rerankers: string[]
  missing: { case_id: string; mode: string }[]
  modes: Record<
    string,
    { overall: RetrievalSummary; by_case_variant: Record<string, RetrievalSummary> }
  >
  note: string
}

// ---- Scenario Studio (gbya.api.routers.scenarios) ----

export interface ScenarioSummary {
  id: string
  window_id: string | null
  target_host: string | null
  status: string
  cases: string[]
  error: string | null
}

export interface Asset {
  host: string
  role: string
  tier: number
  owner?: string | null
}

export interface Ticket {
  id: string
  host: string
  account: string
  command_pattern: string
  start: string
  end: string
  approved: boolean
}

export interface TrustedContextJson {
  schema_version: number
  assets: Asset[]
  identities: Record<string, unknown>[]
  network: Record<string, unknown>
  approval_script: { mode: string }
  change_tickets: Ticket[]
}

export interface ScenarioJson {
  id: string
  window_id: string
  target_host: string
  request: { objective: string; target: Record<string, unknown>; text: string }
  trusted_context: TrustedContextJson
  e1: {
    tool: string
    args: Record<string, unknown>
    cited: number[]
    technique_claimed: string | null
    rationale: string | null
  }
  // Decision A: the request's host(s) plus the processes, accounts and IPs acting on them
  scope: ScopeJson
  suspicious_record_ids: number[]
  [key: string]: unknown
}

export interface ScopeJson {
  hosts: string[]
  processes: { host: string; pid: number; image: string | null }[]
  accounts: string[]
  ips: string[]
}

export interface ScenarioDetail {
  id: string
  status: string
  scenario: ScenarioJson
  cases: { id: string; variant: string; cited: number[]; db_patch: boolean; labelled: boolean }[]
}

export interface ValidationCheck {
  check: string
  case_id: string | null
  status: 'pass' | 'fail' | 'pending' | 'warn'
  message: string
}

export interface ValidationReport {
  scenario_id: string
  valid: boolean
  complete: boolean
  tokenizer: string
  checks: ValidationCheck[]
}

export interface DbDiffJson {
  removed: number[]
  added: { record_id: number; table: string | null; row: Record<string, Cell> | null }[]
  changed: { record_id: number; table: string | null; fields: Record<string, [Cell, Cell]> }[]
}

export interface CaseDetail {
  case: {
    id: string
    variant: string
    package: { tool: string; args: Record<string, unknown>; cited: number[] }
    db_patch: unknown
    r_edit: unknown
    labels: Record<string, unknown>
  }
  db: string
  prefix: {
    profile: string
    queries: { sql: string; result: string; registered: number[] }[]
    findings: string
    retrieved: number[]
  } | null
  diff: DbDiffJson | null
}

// ---- Annotation (gbya.api.routers.annotate) ----

export interface E2Flag {
  record_id: number
  flagged_by: string
  reason: string
  at: string
  replaced_by: number[]
  e2_before: number[]
  e2_after: number[]
}

export interface AnnotateCase {
  e2: {
    sampled: {
      record_id: number
      table: string | null
      host: string | null
      ts: string | null
      pid: number | null
      guid: string | null
      summary: string
    }[]
    flags: E2Flag[]
  } | null
  id: string
  variant: string
  request: { text: string; objective: string; target: Record<string, unknown> }
  package: { tool: string; args: Record<string, unknown>; cited: number[] }
  prompt: {
    messages: { role: string; content: string }[]
    manifest: Record<string, unknown>
    reference_available: boolean
  }
  structured: Record<string, Record<string, Cell>>
  suspicious: {
    record_id: number
    table: string | null
    host: string | null
    account: string | null
    command: string | null
    ts: string | null
  }[]
  tickets: Ticket[]
  policy: { decision: string; rule_id: string }
  approval_script: string
  e4_kind: string | null
  marked_labels?: {
    list: 'permitted' | 'prohibited'
    tool: string
    args: Record<string, unknown>
    note: string
    requires_approval?: boolean
    fulfils?: boolean
  }[]
  injection?: InjectionMarker | null
}

export interface AnnotationOut {
  role: string
  name?: string | null
  labels: Record<string, Record<string, unknown>>
  notes: Record<string, unknown>
  submitted_at: string | null
}

/** The one case of an end-to-end window (set X, decision D): no package and no prompt. */
export interface XCase {
  kind: 'end_to_end'
  id: string
  variant: 'X'
  request: { text: string; objective: string; target: Record<string, unknown> }
  window: { id: string; title: string | null; techniques: string[] }
  trusted_context: TrustedContextJson
  context_author: string | null
  context_checked_by: string | null
  scope: ScopeJson
  tickets: Ticket[]
  policy: { decision: string; rule_id: string; reason?: string }
  approval_script: string
  not_labelled: string[]
  injection?: InjectionMarker | null
}

/** A planted injection marker (team lead, 14 Oct 2026): not evidence; its call is prohibited. */
export interface InjectionMarker {
  record_id: number
  text: string
  token: string
  action: { tool: string; args: Record<string, unknown> }
}

export interface Workspace {
  scenario: {
    id: string
    status: string
    split: string | null
    target_host: string | null
    kind?: 'scenario' | 'end_to_end'
    author?: string | null
  }
  cases: (AnnotateCase | XCase)[]
  own: AnnotationOut | null
  other_submitted: boolean
  both_submitted: boolean
}

export interface AnnotateOverview {
  id: string
  status: string
  split: string | null
  cases: number
  kind?: 'scenario' | 'end_to_end'
  A: string
  B: string
}

export interface Comparison {
  A: AnnotationOut
  B: AnnotationOut
  disagreements: Record<string, string[]>
}

export interface AgreementSummary {
  scenarios: number
  kappa_outcome: number | null
  outcome_items: number
  outcome_agreement: number | null
  kappa_actions: number | null
  action_items: number
  action_agreement: number | null
  jaccard_e1_mean?: number | null
  jaccard_x_mean?: number | null // set X: Jaccard on the evidence records (decision D)
  jaccard_field?: string
  jaccard_n: number
}

export interface AgreementReport {
  split: string | null
  pooled: AgreementSummary // sets E and R
  per_scenario: (AgreementSummary & { scenario_id: string; split: string | null })[]
  set_x?: AgreementSummary // end-to-end windows, reported separately (Q-X2)
  per_scenario_x?: (AgreementSummary & { scenario_id: string; split: string | null })[]
  note: string
}

// ---- Agent Console, Traces and the adjudication queue (T5.6, T5.4) ----------------------------

export type StepKind = 'llm' | 'tool' | 'gate' | 'approval' | 'feedback'

export interface StepOut {
  idx: number
  kind: StepKind
  // The step payload as stored (append-only); its shape depends on the kind.
  payload: Record<string, unknown>
  ms?: number | null
}

export interface EpisodeSummary {
  id: number
  run_id: number | null
  case_id: string | null
  system: string
  run_idx: number
  temperature: number | null
  seed: number | null
  status: string
  terminal_state: string | null
  outcome: string | null
  rule: string | null
  rule_text: string | null
  budget_exhausted: boolean
  unqualified_escalation: boolean
  tokens_in: number
  tokens_out: number
  interactive_approval: boolean
  error: string | null
  // Exp 3 / Console window mode: the live investigation that preceded the response (T6.3)
  investigation?: Investigation | null
  tags?: string[]
  starred?: boolean
}

export interface EpisodeDetail extends EpisodeSummary {
  steps: StepOut[]
  tool_calls: {
    tool: string
    class: string
    args: Record<string, unknown>
    cited: number[]
    admitted: boolean
    call_class: string | null
    escalation_class: string | null
  }[]
  scoring: Record<string, unknown> | null
  summary: Record<string, unknown> | null
  request: { objective: string; target: Record<string, unknown>; text: string } | null
  labels: Record<string, unknown> | null
  pending_approval: { call_key: string; call: string } | null
  tags: string[]
  starred: boolean
  note: string | null
  error_tags: string[]
  suggested_tags?: string[] // implied by the trace and labels; a person confirms (T7.5)
}

export interface ConsoleCase {
  case_id: string
  variant: string
  request: { objective: string; target: Record<string, unknown>; text: string }
  labelled: boolean
  // case: Exp 2 frozen prefix; window: end-to-end case, investigated live (Exp 3)
  mode: 'case' | 'window'
  window_id: string | null
}

export interface Investigation {
  findings: { technique_id: string; behaviour: string; cited: number[] }[]
  predicted_techniques: string[]
  hypotheses: {
    technique_id: string
    behaviour: string
    verdict: string | null
    cited: number[]
    reason: string
  }[]
  llm_calls: number
  budget_exhausted: boolean
  handover_stubbed: string[]
}

export interface ConsoleSystem {
  id: string
  checks: string[]
  recovery_budget: number | null
  verifier_variant: string | null
  unavailable: string | null
}

export interface AdjudicationItem {
  ref_id: string
  case_id: string
  kind: 'call' | 'escalation' | 'technique'
  tool: string
  item: string
  episodes: number
}

// ---- Results and analysis (T7.1, T7.2, T7.4) ------------------------------------------------
// Proportions are authoritative; *_pp fields (percentage points) are for display only.

export interface PropOut {
  total: number
  n: number
  rate: number | null
  rate_pp: number | null
}

export type Maybe<T> = T | { refused: string }

export interface Exp1SystemResults {
  wrongly_admitted_e2_e5: Maybe<PropOut>
  wrongly_admitted_by_variant: Record<string, Maybe<PropOut>>
  wrongly_blocked: Maybe<PropOut>
  check_failures: Record<string, number>
  decisions: number
}

export interface EpisodeSystemResults {
  episodes: number
  outcome_rates: Maybe<Record<string, PropOut>>
  task_success: Maybe<PropOut>
  unsafe_proposed_vs_admitted: Maybe<PropOut>
  permitted_wrongly_blocked: Maybe<PropOut>
  tool_call_accuracy: PropOut
  invalid_outputs: number
  e2_recovery: PropOut & { mean_proposals: number | null }
  // episodes on cases with an injection marker: its call proposed / admitted by the gate
  injection_compliance: {
    episodes: number
    proposed: PropOut
    admitted: PropOut
    token_echoed: PropOut
  }
  log_deletion_attempts: number
  evidence: { n: number; precision: number | null; recall: number | null }
  latency: { n: number; mean_ms: number | null; p50_ms: number | null; p95_ms: number | null }
}

export interface RunResults {
  run_id: number
  experiment: number
  units: 'proportion'
  purpose: string
  replay: boolean
  research_eligible: boolean
  by_system: Record<string, Exp1SystemResults | EpisodeSystemResults>
}

export interface IntervalOut {
  estimate: number
  ci_low: number
  ci_high: number
  estimate_pp: number
  ci_low_pp: number
  ci_high_pp: number
  n_scenarios: number
  n_cases: number
}

export interface HypothesisOut extends IntervalOut {
  target: number
  target_pp: number
  target_reached: boolean
  statistically_supported: boolean
}

export interface H2Out extends IntervalOut {
  margin: number
  margin_pp: number
  decision: 'no_harm_supported' | 'harm_beyond_margin' | 'inconclusive'
}

export interface Provenance {
  run_id: number
  experiment: number
  purpose: string
  status: string
  replay: boolean
  git_sha: string | null
  config_hash: string
  case_set_hash: string | null
  model_id: string | null
  model_file_sha256: string | null
  backend: string | null
  research_eligible: boolean
  created_at: string | null
  finished_at: string | null
}

export interface HypothesesOut {
  units: 'proportion'
  H1: HypothesisOut
  H1_C4: HypothesisOut
  H2: H2Out
  exploratory: { code_ticket: Record<string, unknown> }
  bootstrap: { B: number; seed: number }
  provenance: { exp1: Provenance; exp2: Provenance }
}

export interface IllustrativeOut {
  illustrative: true
  rates: Record<string, Record<string, number>>
  differences: (Omit<IntervalOut, 'estimate_pp' | 'ci_low_pp' | 'ci_high_pp'> & {
    name: string
    estimate_pp: number | null
    ci_low_pp: number | null
    ci_high_pp: number | null
  })[]
  refused: Record<string, string>
  provenance: Provenance
  note: string
}

export interface Taxonomy {
  tags: string[]
  by_system: Record<string, Record<string, number>>
  starred: {
    episode_id: number
    case_id: string
    system: string
    tags: string[]
    note: string | null
  }[]
  starred_target: number
}
