import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'

import {
  API_BASE,
  apiGet,
  apiPost,
  type Accuracy,
  type Budget,
  type OutcomesOut,
  type Progress,
  type RetrievalOut,
  type RunItem,
  type RunOut,
  type VerifierEvalOut,
} from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'

const TERMINAL = new Set(['completed', 'cancelled', 'failed'])

/** Live progress from the SSE stream ``/runs/{id}/progress`` (one event per second). */
function useProgress(runId: string, initial: Progress | undefined): Progress | undefined {
  const [live, setLive] = useState<Progress | undefined>(undefined)
  const status = initial?.status
  useEffect(() => {
    if (!status || TERMINAL.has(status) || typeof EventSource === 'undefined') return
    const es = new EventSource(`${API_BASE}/runs/${runId}/progress`)
    es.addEventListener('progress', (e) => {
      const p = JSON.parse((e as MessageEvent<string>).data) as Progress
      setLive(p)
      if (TERMINAL.has(p.status)) es.close()
    })
    es.onerror = () => es.close()
    return () => es.close()
  }, [runId, status])
  // The stream's latest event wins over the fetched snapshot while it is newer.
  return live && initial && live.done >= initial.done ? live : (initial ?? live)
}

const pct = (x: number | null) => (x === null ? '—' : x.toFixed(3))

export function ExperimentRun() {
  const { runId = '' } = useParams()
  const qc = useQueryClient()
  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => apiGet<RunOut>(`/runs/${runId}`),
  })
  const progress = useProgress(runId, run.data?.progress)
  const finished = progress ? TERMINAL.has(progress.status) : false
  const exp = run.data?.experiment
  const evalQ = useQuery({
    queryKey: ['verifier-eval', runId, progress?.status],
    queryFn: () => apiGet<VerifierEvalOut>(`/runs/${runId}/verifier-eval`),
    enabled: finished && exp === 1,
  })
  const retrievalQ = useQuery({
    queryKey: ['retrieval', runId, progress?.status],
    queryFn: () => apiGet<RetrievalOut>(`/runs/${runId}/retrieval`),
    enabled: finished && exp === 1,
    retry: false,
  })
  const outcomesQ = useQuery({
    queryKey: ['outcomes', runId, progress?.done],
    queryFn: () => apiGet<OutcomesOut>(`/runs/${runId}/outcomes`),
    enabled: exp === 2,
  })
  const errors = useQuery({
    queryKey: ['run-errors', runId, progress?.errors],
    queryFn: () => apiGet<RunItem[]>(`/runs/${runId}/items`, { status: 'error', limit: 50 }),
    enabled: (progress?.errors ?? 0) > 0,
  })
  const control = useMutation({
    mutationFn: (action: 'start' | 'cancel' | 'resume') =>
      apiPost<RunOut>(`/runs/${runId}/${action}`, {}),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['run', runId] }),
  })

  if (run.isError) return <ErrorBanner error={run.error} />
  if (!run.data) return <p>Loading…</p>
  const r = run.data
  const p = progress ?? r.progress
  return (
    <section aria-labelledby="run-title">
      <h1 id="run-title" className="mb-2 text-2xl font-semibold">
        Run {r.id}: Experiment {r.experiment}
      </h1>
      <p className="mb-2 text-sm">
        <Link className="text-blue-800 underline" to={`/results/${r.id}`}>
          Open the results of run {r.id}
        </Link>
      </p>
      <div aria-live="polite" className="mb-3 text-sm">
        <p>
          Status: <strong className="font-mono">{p.status}</strong>; {p.done} of {p.total} units
          done; {p.errors} errors
          {p.eta_s !== null && <>; ETA {Math.round(p.eta_s)} s</>}
          {p.tps_in !== null && (
            <>
              ; {p.tps_in} prompt tok/s, {p.tps_out} output tok/s
            </>
          )}
        </p>
        <progress className="w-full max-w-xl" max={p.total} value={p.done}>
          {p.done}/{p.total}
        </progress>
        {p.budget && <BudgetLine b={p.budget} />}
      </div>
      <div className="mb-4 flex gap-2">
        {(['start', 'cancel', 'resume'] as const).map((a) => (
          <button
            key={a}
            type="button"
            className="rounded border border-gray-600 px-3 py-1 text-sm capitalize"
            onClick={() => control.mutate(a)}
            disabled={control.isPending}
          >
            {a}
          </button>
        ))}
      </div>
      {control.isError && <ErrorBanner error={control.error} />}
      <h2 className="mb-1 text-lg font-semibold">Provenance</h2>
      <dl className="mb-4 grid max-w-4xl grid-cols-[max-content_1fr] gap-x-4 text-sm">
        <dt className="font-semibold">Purpose</dt>
        <dd>
          {r.purpose}
          {r.research_eligible ? ' (research result)' : ' (not a research result)'}
        </dd>
        <dt className="font-semibold">Backend / model</dt>
        <dd className="font-mono">
          {r.backend} · {r.model_id ?? '—'}
        </dd>
        <dt className="font-semibold">Weights digest</dt>
        <dd className="font-mono">{r.model_file_sha256 ?? '—'}</dd>
        <dt className="font-semibold">Git</dt>
        <dd className="font-mono">{r.git_sha ?? '—'}</dd>
        <dt className="font-semibold">Config hash</dt>
        <dd className="font-mono">{r.config_hash.slice(0, 16)}</dd>
        <dt className="font-semibold">Cases</dt>
        <dd className="font-mono">{(r.config.case_ids ?? []).join(', ')}</dd>
      </dl>
      {errors.data && errors.data.length > 0 && (
        <>
          <h2 className="mb-1 text-lg font-semibold">Failed units</h2>
          <ul className="mb-4 list-disc pl-6 text-sm">
            {errors.data.map((e) => (
              <li key={`${e.case_id}-${e.system}-${e.run_idx}`}>
                <span className="font-mono">
                  {e.case_id} · {e.system} · run {e.run_idx}
                </span>
                : {e.error}
              </li>
            ))}
          </ul>
        </>
      )}
      {outcomesQ.data && <Outcomes data={outcomesQ.data} />}
      {evalQ.data && <VerifierEval data={evalQ.data} />}
      {evalQ.data && retrievalQ.data && (
        <RetrievalLevels verifier={evalQ.data} retrieval={retrievalQ.data} />
      )}
    </section>
  )
}

const hours = (h: number) => h.toFixed(h < 10 ? 2 : 1)

/** Budget monitor (T5.5): projected GPU-hours against the budget; F1 offered, never applied. */
export function BudgetLine({ b }: { b: Budget }) {
  return (
    <div className="mt-2 max-w-3xl">
      <p>
        Budget: this run {hours(b.run_h)} h projected ({hours(b.elapsed_h)} h so far); other
        research runs {hours(b.spent_other_research_h)} h; projected total{' '}
        <strong>{hours(b.projected_total_h)} h</strong> of {hours(b.limit_h)} GPU-hours.
      </p>
      {b.over && (
        <div role="alert" className="mt-1 rounded border border-red-700 bg-red-50 p-2 text-red-900">
          <p className="font-semibold">Projected total exceeds the budget.</p>
          {b.offer ? (
            <p>
              Fallback {b.offer.fallback} (experiments/fallbacks.yaml) would drop{' '}
              {b.offer.drop_systems.join(', ')} and run{' '}
              {Object.entries(b.offer.runs)
                .map(([s, n]) => `${s} ×${n}`)
                .join(', ')}
              : {b.offer.units} units, about {hours(b.offer.run_h)} h for this run (projected total{' '}
              {hours(b.offer.projected_total_h)} h). Applying it is a team decision: set{' '}
              <code>fallback: {b.offer.fallback}</code> in experiments/exp2.yaml, record it in
              STATUS.md and create a new run.
            </p>
          ) : (
            <p>No fallback applies to this run; discuss the schedule with the team.</p>
          )}
        </div>
      )}
    </div>
  )
}

function Outcomes({ data }: { data: OutcomesOut }) {
  const th = 'border-b border-gray-400 px-2 py-1 text-left'
  const kinds = ['safe_completion', 'justified_escalation', 'unnecessary_deferral', 'refusal',
    'unsafe_execution', 'pending_adjudication', 'unscored'] // prettier-ignore
  return (
    <>
      <h2 className="mb-1 text-lg font-semibold">Exp 2: outcomes per system</h2>
      <p className="mb-1 text-xs text-gray-700">
        Episode counts. Unscored: the case&apos;s labels are pending. Pending adjudication: an
        unlisted call or escalation waits in the adjudication queue.
      </p>
      <table className="mb-4 min-w-full border-collapse text-sm">
        <caption className="sr-only">Episode outcomes per system</caption>
        <thead>
          <tr>
            {['System', ...kinds].map((h) => (
              <th key={h} scope="col" className={th}>
                {h.replaceAll('_', ' ')}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(data.by_system).map(([sys, counts]) => (
            <tr key={sys} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left font-mono">
                {sys}
              </th>
              {kinds.map((k) => (
                <td key={k} className="px-2 py-1">
                  {counts[k] ?? 0}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mb-4 text-sm">
        Task success rate (safe completion or justified escalation):{' '}
        {data.task_success?.refused
          ? `not computed: ${data.task_success.refused}`
          : Object.entries(data.task_success?.by_system ?? {})
              .map(([s, r]) => `${s} ${r.rate_pp ?? '—'} pp (n ${r.n})`)
              .join('; ')}
      </p>
      {data.citation_gap && data.citation_gap.by_variant_system.length > 0 && (
        <>
          <h3 className="mb-1 font-semibold">Retrieved but not cited</h3>
          <p className="mb-1 text-xs text-gray-700">
            The gate only sees cited records. Episodes in which the agent retrieved one of the
            case&apos;s decisive (or contradicting) records and no gate-checked call cited it.
          </p>
          <table className="mb-4 min-w-full border-collapse text-sm">
            <caption className="sr-only">Retrieved but not cited, per variant and system</caption>
            <thead>
              <tr>
                {[
                  'Variant',
                  'System',
                  'Episodes',
                  'Retrieved a decisive record',
                  'Retrieved but not cited',
                  'Share',
                ].map((h) =>
                  // prettier-ignore
                  <th key={h} scope="col" className={th}>
                    {h}
                  </th>,
                )}
              </tr>
            </thead>
            <tbody>
              {data.citation_gap.by_variant_system.map((g) => (
                <tr key={`${g.variant}-${g.system}`} className="border-b border-gray-200">
                  <td className="px-2 py-1">{g.variant}</td>
                  <td className="px-2 py-1 font-mono">{g.system}</td>
                  <td className="px-2 py-1">{g.episodes}</td>
                  <td className="px-2 py-1">{g.retrieved}</td>
                  <td className="px-2 py-1">{g.retrieved_not_cited}</td>
                  <td className="px-2 py-1">{pct(g.rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  )
}

function AccuracyCells({ a }: { a: Accuracy }) {
  return (
    <>
      <td className="px-2 py-1">{a.n}</td>
      <td className="px-2 py-1">{pct(a.exact_accuracy)}</td>
      <td className="px-2 py-1">{pct(a.binary_accuracy)}</td>
    </>
  )
}

function VerifierEval({ data }: { data: VerifierEvalOut }) {
  const th = 'border-b border-gray-400 px-2 py-1 text-left'
  return (
    <>
      <h2 className="mb-1 text-lg font-semibold">Exp 1V: diagnostic verifier accuracy</h2>
      <p className="mb-1 text-xs text-gray-700">
        Over every package, whatever C1–C3 would say. Proportions; n is the denominator.
        {!data.research_eligible && ' Not a research result.'}
      </p>
      <table className="mb-4 min-w-full border-collapse text-sm">
        <caption className="sr-only">Verifier accuracy by variant and case variant</caption>
        <thead>
          <tr>
            {['Variant', 'Case variant', 'n', 'Exact', 'Binary', 'Parse errors'].map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(data.diagnostic).flatMap(([v, d]) => [
            <tr key={v} className="border-b border-gray-300 font-semibold">
              <th scope="row" className="px-2 py-1 text-left font-mono">
                {v}
              </th>
              <td className="px-2 py-1">all</td>
              <AccuracyCells a={d} />
              <td className="px-2 py-1">{d.parse_errors}</td>
            </tr>,
            ...Object.entries(d.by_case_variant).map(([cv, a]) => (
              <tr key={`${v}-${cv}`} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left font-mono font-normal">
                  {v}
                </th>
                <td className="px-2 py-1">{cv}</td>
                <AccuracyCells a={a} />
                <td className="px-2 py-1" />
              </tr>
            )),
          ])}
        </tbody>
      </table>
      {data.e2_c3 && Object.keys(data.e2_c3).length > 0 && (
        <>
          <h2 className="mb-1 text-lg font-semibold">C3 rejections on E2, by tool</h2>
          <p className="mb-1 text-xs text-gray-700">
            E2 cites benign records on the target host. A high rate (expected for kill_process)
            means E2 tests C3 rather than the verifier for that tool.
          </p>
          <table className="mb-4 min-w-full border-collapse text-sm">
            <caption className="sr-only">C3 rejection rate on E2 per system and tool</caption>
            <thead>
              <tr>
                {['System', 'Tool', 'n', 'Rejected at C3', 'Rate'].map((h) => (
                  <th key={h} scope="col" className={th}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.e2_c3).flatMap(([sys, tools]) =>
                Object.entries(tools).map(([tool, v]) => (
                  <tr key={`${sys}-${tool}`} className="border-b border-gray-200">
                    <th scope="row" className="px-2 py-1 text-left font-mono">
                      {sys}
                    </th>
                    <td className="px-2 py-1 font-mono">{tool}</td>
                    <td className="px-2 py-1">{v.n}</td>
                    <td className="px-2 py-1">{v.c3_rejected}</td>
                    <td className="px-2 py-1">{pct(v.rate)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </>
      )}
      <h2 className="mb-1 text-lg font-semibold">Exp 1G: packages reaching C4</h2>
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">C4 invocation counts per gate configuration</caption>
        <thead>
          <tr>
            {['System', 'Packages', 'Reached C4', 'n', 'Exact (conditional)', 'Binary'].map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Object.entries(data.gate_path).map(([s, g]) => (
            <tr key={s} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left font-mono">
                {s}
              </th>
              <td className="px-2 py-1">{g.packages}</td>
              <td className="px-2 py-1">{g.reached_c4}</td>
              <AccuracyCells a={g.conditional} />
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

// The three retrieval levels of the RAG ablation (§D.3): no reference (A4), BM25 (G3), BM25 +
// cross-encoder rerank (A6) — retrieval quality and the verifier accuracy it leads to.
const LEVELS = [
  { label: 'No RAG (A4)', mode: null, variant: 'none' },
  { label: 'BM25 (G3)', mode: 'bm25', variant: 'standard' },
  { label: 'BM25 + rerank (A6)', mode: 'bm25_rerank', variant: 'rerank' },
] as const

function RetrievalLevels({
  verifier,
  retrieval,
}: {
  verifier: VerifierEvalOut
  retrieval: RetrievalOut
}) {
  const th = 'border-b border-gray-400 px-2 py-1 text-left'
  const heads = ['Level', 'Recall@5', 'Hit@5', 'nDCG@5', 'MRR@20', 'ATT&CK top-1', 'Mean |G|',
    'Excluded', 'Verifier n', 'Verifier exact', 'Verifier binary'] // prettier-ignore
  return (
    <>
      <h2 className="mt-4 mb-1 text-lg font-semibold">Retrieval levels (RAG ablation)</h2>
      <p className="mb-1 text-xs text-gray-700">{retrieval.note}</p>
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">
          Retrieval metrics and verifier accuracy per retrieval level
        </caption>
        <thead>
          <tr>
            {heads.map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {LEVELS.map((lv) => {
            const m = lv.mode ? retrieval.modes[lv.mode]?.overall : undefined
            const v = verifier.diagnostic[lv.variant]
            return (
              <tr key={lv.label} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left">
                  {lv.label}
                </th>
                <td className="px-2 py-1">{m ? pct(m.recall_at_5) : 'n/a'}</td>
                <td className="px-2 py-1">{m ? pct(m.hit_at_5) : 'n/a'}</td>
                <td className="px-2 py-1">{m ? pct(m.ndcg_at_5) : 'n/a'}</td>
                <td className="px-2 py-1">{m ? pct(m.mrr_at_20) : 'n/a'}</td>
                <td className="px-2 py-1">{m ? pct(m.attack_top1) : 'n/a'}</td>
                <td className="px-2 py-1">{m?.mean_gold_size?.toFixed(1) ?? 'n/a'}</td>
                <td className="px-2 py-1">{m ? m.excluded_empty_gold : 'n/a'}</td>
                <td className="px-2 py-1">{v ? v.n : '—'}</td>
                <td className="px-2 py-1">{v ? pct(v.exact_accuracy) : '—'}</td>
                <td className="px-2 py-1">{v ? pct(v.binary_accuracy) : '—'}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </>
  )
}
