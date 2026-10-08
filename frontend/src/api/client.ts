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

// ---- Types mirroring the backend models (gbya.api.routers.*) ----

export interface Health {
  api: string
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
}

export interface RunItem {
  case_id: string
  system: string
  run_idx: number
  status: string
  attempts: number
  error: string | null
}
