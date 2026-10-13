import { useMutation, useQuery } from '@tanstack/react-query'
import { useId, useState } from 'react'
import { Link, useParams } from 'react-router'

import {
  apiGet,
  apiPost,
  type EpisodeSystemResults,
  type Exp1SystemResults,
  type H2Out,
  type HypothesesOut,
  type HypothesisOut,
  type IllustrativeOut,
  type Maybe,
  type PropOut,
  type Provenance,
  type RunOut,
  type RunResults,
} from '../api/client'
import { Forest, type ForestRow } from '../charts/Forest'
import { OutcomeBars } from '../charts/OutcomeBars'
import { OUTCOMES } from '../charts/outcomes'
import { ErrorBanner } from '../components/ErrorBanner'

/** Values arrive as proportions; the page prints percentage points with the unit. */
const pp = (x: number | null | undefined) =>
  x === null || x === undefined ? '—' : `${(x * 100).toFixed(1)} pp`
const th = 'border-b border-gray-400 px-2 py-1 text-left'
const refused = (v: Maybe<unknown>): v is { refused: string } =>
  typeof v === 'object' && v !== null && 'refused' in v
const cell = (v: Maybe<PropOut> | undefined) =>
  v === undefined ? '—' : refused(v) ? `refused: ${v.refused}` : `${pp(v.rate)} (n ${v.n})`

/** The runs, each linking to its results. */
export function ResultsIndex() {
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => apiGet<RunOut[]>('/runs') })
  return (
    <section aria-labelledby="ri-title">
      <h1 id="ri-title" className="mb-2 text-2xl font-semibold">
        Results
      </h1>
      <p className="mb-3 text-sm">
        Choose a run. Research verdicts exist only for completed research runs on the frozen case
        set; every other run is shown in illustrative mode.
      </p>
      {runs.isError && <ErrorBanner error={runs.error} />}
      <table className="min-w-full border-collapse text-sm">
        <caption className="sr-only">Runs with results</caption>
        <thead>
          <tr>
            {['Run', 'Experiment', 'Purpose', 'Status', 'Research'].map((h) => (
              <th key={h} scope="col" className={th}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(runs.data ?? []).map((r) => (
            <tr key={r.id} className="border-b border-gray-200">
              <th scope="row" className="px-2 py-1 text-left">
                <Link className="text-blue-800 underline" to={`/results/${r.id}`}>
                  Results for run {r.id}
                </Link>
              </th>
              <td className="px-2 py-1">{r.experiment}</td>
              <td className="px-2 py-1">{r.purpose}</td>
              <td className="px-2 py-1">{r.status}</td>
              <td className="px-2 py-1">{r.research_eligible ? 'yes' : 'illustrative'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

/** Results of one run (plan §E.1 row 8, T7.4, P1 scope). */
export function Results() {
  const { runId = '' } = useParams()
  const run = useQuery({
    queryKey: ['run', runId],
    queryFn: () => apiGet<RunOut>(`/runs/${runId}`),
  })
  const res = useQuery({
    queryKey: ['results', runId],
    queryFn: () => apiGet<RunResults>(`/runs/${runId}/results`),
  })
  if (run.isError) return <ErrorBanner error={run.error} />
  if (!run.data) return <p>Loading…</p>
  const r = run.data
  return (
    <section aria-labelledby="rs-title" className="relative">
      {!r.research_eligible && (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-0 flex items-center justify-center text-7xl font-black tracking-widest text-gray-500/10 select-none"
          style={{ transform: 'rotate(-20deg)' }}
        >
          ILLUSTRATIVE
        </div>
      )}
      <div className="relative z-10">
        <h1 id="rs-title" className="mb-2 text-2xl font-semibold">
          Results for run {r.id}: Experiment {r.experiment}
        </h1>
        {!r.research_eligible && (
          <p
            role="note"
            className="mb-3 rounded border border-amber-700 bg-amber-50 p-2 text-sm text-amber-950"
          >
            <strong>Illustrative mode — not a research result.</strong> This run is {r.purpose}
            {r.replay ? ', a replay' : ''}
            {r.case_set_hash ? '' : ', not on the frozen case set'}: estimates and intervals only,
            no verdicts.
          </p>
        )}
        <ProvenancePanel run={r} />
        {r.research_eligible ? <HypothesisPanel run={r} /> : <IllustrativePanel run={r} />}
        {res.isError && <ErrorBanner error={res.error} />}
        {res.data && <Tables data={res.data} />}
      </div>
    </section>
  )
}

function ProvenancePanel({ run }: { run: RunOut }) {
  const rows: [string, string | null | undefined][] = [
    ['Purpose', run.purpose],
    ['Status', run.status],
    ['Research-eligible', run.research_eligible ? 'yes' : 'no'],
    ['Replay', run.replay ? 'yes' : 'no'],
    ['Git SHA', run.git_sha],
    ['Config hash', run.config_hash],
    ['Case-set hash', run.case_set_hash],
    ['Model', run.model_id],
    ['Model file SHA-256', run.model_file_sha256],
    ['Backend', run.backend],
    ['Created', run.created_at],
    ['Finished', run.finished_at],
  ]
  return (
    <details open className="mb-3 rounded border border-gray-400 p-2 text-sm">
      <summary className="cursor-pointer font-semibold">Provenance</summary>
      <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="font-semibold">{k}</dt>
            <dd className="font-mono text-xs break-all">{v ?? '—'}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

function HypothesisCard({ title, h }: { title: string; h: HypothesisOut }) {
  return (
    <article aria-label={title} className="rounded border border-gray-500 p-2 text-sm">
      <h3 className="font-semibold">{title}</h3>
      <p>
        Estimate <strong>{pp(h.estimate)}</strong> (95% CI {pp(h.ci_low)} to {pp(h.ci_high)})
      </p>
      <p>
        Target {pp(h.target)}: {h.target_reached ? 'reached' : 'not reached'}; statistically
        supported: {h.statistically_supported ? 'yes' : 'no'}
      </p>
      <p className="text-xs text-gray-700">
        {h.n_scenarios} scenarios, {h.n_cases} cases
      </p>
    </article>
  )
}

function H2Card({ h }: { h: H2Out }) {
  return (
    <article aria-label="H2" className="rounded border border-gray-500 p-2 text-sm">
      <h3 className="font-semibold">H2 — deferral, G3 − G1 (justified-completion cases)</h3>
      <p>
        Estimate <strong>{pp(h.estimate)}</strong> (95% CI {pp(h.ci_low)} to {pp(h.ci_high)})
      </p>
      <p>
        Margin {pp(h.margin)}: <strong>{h.decision.replaceAll('_', ' ')}</strong>
      </p>
      <p className="text-xs text-gray-700">
        {h.n_scenarios} scenarios, {h.n_cases} cases
      </p>
    </article>
  )
}

function HypothesisPanel({ run }: { run: RunOut }) {
  const other = run.experiment === 1 ? 2 : 1
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => apiGet<RunOut[]>('/runs') })
  const partners = (runs.data ?? []).filter(
    (r) => r.experiment === other && r.research_eligible && r.status === 'completed',
  )
  const [partner, setPartner] = useState<number | ''>('')
  const id = useId()
  const compute = useMutation({
    mutationFn: () =>
      apiPost<HypothesesOut>('/analysis/hypotheses', {
        run_ids:
          run.experiment === 1 ? { exp1: run.id, exp2: partner } : { exp1: partner, exp2: run.id },
      }),
  })
  const d = compute.data
  const forest: ForestRow[] = d
    ? [
        { name: 'H1 (G1 − G3, E2–E5)', ...d.H1, target: d.H1.target },
        { name: 'H1-C4 (A1 − G3, E4–E5)', ...d.H1_C4, target: d.H1_C4.target },
        { name: 'H2 (G3 − G1 deferral)', ...d.H2, target: d.H2.margin },
      ]
    : []
  return (
    <section aria-labelledby="hy-title" className="mb-4">
      <h2 id="hy-title" className="text-lg font-semibold">
        Hypotheses
      </h2>
      <div className="mb-2 flex flex-wrap items-end gap-2 text-sm">
        <label htmlFor={id} className="flex flex-col">
          Experiment {other} research run
          <select
            id={id}
            className="rounded border border-gray-500 p-1"
            value={partner}
            onChange={(e) => setPartner(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">Choose…</option>
            {partners.map((p) => (
              <option key={p.id} value={p.id}>
                run {p.id}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="rounded bg-blue-800 px-3 py-1 text-white disabled:opacity-60"
          disabled={partner === '' || compute.isPending}
          onClick={() => compute.mutate()}
        >
          Compute (bootstrap, B = 10,000)
        </button>
      </div>
      {compute.isError && <ErrorBanner error={compute.error} />}
      {d && (
        <>
          <div className="mb-2 grid grid-cols-1 gap-2 md:grid-cols-3">
            <HypothesisCard title="H1 — wrongly admitted, G1 − G3 (E2–E5)" h={d.H1} />
            <HypothesisCard title="H1-C4 — wrongly admitted, A1 − G3 (E4–E5)" h={d.H1_C4} />
            <H2Card h={d.H2} />
          </div>
          <Forest rows={forest} title="Differences with 95% intervals" />
          <details className="text-sm">
            <summary className="cursor-pointer">
              Exploratory: code-computed ticket scope (not used for H1, H1-C4 or H2)
            </summary>
            <pre
              tabIndex={0}
              aria-label="Code-ticket table"
              className="overflow-auto bg-gray-100 p-2 text-xs"
            >
              {JSON.stringify(d.exploratory.code_ticket, null, 2)}
            </pre>
          </details>
          <ProvenanceList prov={[d.provenance.exp1, d.provenance.exp2]} />
        </>
      )}
    </section>
  )
}

function ProvenanceList({ prov }: { prov: Provenance[] }) {
  return (
    <p className="text-xs text-gray-700">
      Computed from{' '}
      {prov
        .map(
          (p) =>
            `run ${p.run_id} (Exp ${p.experiment}, git ${p.git_sha ?? '—'}, case set ${p.case_set_hash ?? '—'})`,
        )
        .join(' and ')}
      .
    </p>
  )
}

function IllustrativePanel({ run }: { run: RunOut }) {
  const ill = useQuery({
    queryKey: ['illustrative', run.id],
    queryFn: () => apiPost<IllustrativeOut>('/analysis/illustrative', { run_id: run.id, B: 2000 }),
  })
  const rows: ForestRow[] = (ill.data?.differences ?? []).map((d) => ({
    name: d.name,
    estimate: d.estimate,
    ci_low: d.ci_low,
    ci_high: d.ci_high,
  }))
  return (
    <section aria-labelledby="il-title" className="mb-4">
      <h2 id="il-title" className="text-lg font-semibold">
        Illustrative estimates (no verdicts)
      </h2>
      {ill.isError && <ErrorBanner error={ill.error} />}
      {ill.data && (
        <>
          {rows.length > 0 ? (
            <>
              <ul className="mb-2 text-sm">
                {ill.data.differences.map((d) => (
                  <li key={d.name}>
                    {d.name}: <strong>{pp(d.estimate)}</strong> (95% interval {pp(d.ci_low)} to{' '}
                    {pp(d.ci_high)}; {d.n_scenarios} scenarios, {d.n_cases} cases)
                  </li>
                ))}
              </ul>
              <Forest rows={rows} title="Illustrative differences with 95% intervals" />
            </>
          ) : (
            <p className="text-sm">No paired difference can be computed for this run.</p>
          )}
          {Object.entries(ill.data.refused).map(([k, v]) => (
            <p key={k} className="text-xs text-gray-700">
              {k}: {v}
            </p>
          ))}
        </>
      )}
    </section>
  )
}

const TABLES_1 = ['Wrongly admitted', 'Wrongly blocked and check failures'] as const
const TABLES_2 = ['Outcomes', 'Capability metrics'] as const

function Tables({ data }: { data: RunResults }) {
  const names: readonly string[] = data.experiment === 1 ? TABLES_1 : TABLES_2
  const [shown, setShown] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(names.map((n) => [n, true])),
  )
  return (
    <section aria-labelledby="tb-title">
      <h2 id="tb-title" className="text-lg font-semibold">
        Tables
      </h2>
      <fieldset className="mb-2 flex flex-wrap gap-3 text-sm">
        <legend className="sr-only">Show tables</legend>
        {names.map((n) => (
          <label key={n} className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={shown[n]}
              onChange={(e) => setShown((s) => ({ ...s, [n]: e.target.checked }))}
            />
            {n}
          </label>
        ))}
      </fieldset>
      {data.experiment === 1 ? (
        <Exp1Tables data={data} shown={shown} />
      ) : (
        <EpisodeTables data={data} shown={shown} />
      )}
    </section>
  )
}

function Exp1Tables({ data, shown }: { data: RunResults; shown: Record<string, boolean> }) {
  const by = data.by_system as Record<string, Exp1SystemResults>
  const variants = ['E2', 'E3', 'E4', 'E5']
  return (
    <>
      {shown['Wrongly admitted'] && (
        <table className="mb-4 min-w-full border-collapse text-sm">
          <caption className="text-left font-semibold">
            Wrongly admitted (packages labelled reject)
          </caption>
          <thead>
            <tr>
              {['System', 'E2–E5', ...variants].map((h) => (
                <th key={h} scope="col" className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(by).map(([s, v]) => (
              <tr key={s} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left font-mono">
                  {s}
                </th>
                <td className="px-2 py-1">{cell(v.wrongly_admitted_e2_e5)}</td>
                {variants.map((x) => (
                  <td key={x} className="px-2 py-1">
                    {cell(v.wrongly_admitted_by_variant[x])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {shown['Wrongly blocked and check failures'] && (
        <table className="mb-4 min-w-full border-collapse text-sm">
          <caption className="text-left font-semibold">
            Wrongly blocked and the check that stopped each package
          </caption>
          <thead>
            <tr>
              {['System', 'Wrongly blocked (labelled admit)', 'Check failures', 'Decisions'].map(
                (h) => (
                  <th key={h} scope="col" className={th}>
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {Object.entries(by).map(([s, v]) => (
              <tr key={s} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left font-mono">
                  {s}
                </th>
                <td className="px-2 py-1">{cell(v.wrongly_blocked)}</td>
                <td className="px-2 py-1 font-mono text-xs">
                  {Object.entries(v.check_failures)
                    .map(([k, n]) => `${k} ${n}`)
                    .join(', ') || 'none'}
                </td>
                <td className="px-2 py-1">{v.decisions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

function EpisodeTables({ data, shown }: { data: RunResults; shown: Record<string, boolean> }) {
  const by = data.by_system as Record<string, EpisodeSystemResults>
  const bars: Record<string, Record<string, number>> = {}
  for (const [s, v] of Object.entries(by)) {
    const rates = v.outcome_rates
    if (!refused(rates)) bars[s] = Object.fromEntries(OUTCOMES.map((o) => [o, rates[o]?.rate ?? 0]))
  }
  const ms = (x: number | null) => (x === null ? '—' : `${x.toFixed(0)} ms`)
  return (
    <>
      {shown['Outcomes'] && (
        <>
          {Object.keys(bars).length > 0 && <OutcomeBars data={bars} />}
          <table className="mb-4 min-w-full border-collapse text-sm">
            <caption className="text-left font-semibold">Outcome rates and task success</caption>
            <thead>
              <tr>
                {['System', ...OUTCOMES.map((o) => o.replaceAll('_', ' ')), 'Task success'].map(
                  (h) => (
                    <th key={h} scope="col" className={th}>
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {Object.entries(by).map(([s, v]) => (
                <tr key={s} className="border-b border-gray-200">
                  <th scope="row" className="px-2 py-1 text-left font-mono">
                    {s}
                  </th>
                  {refused(v.outcome_rates) ? (
                    <td colSpan={5} className="px-2 py-1">
                      refused: {v.outcome_rates.refused}
                    </td>
                  ) : (
                    OUTCOMES.map((o) => {
                      const rates = v.outcome_rates as Record<string, PropOut>
                      return (
                        <td key={o} className="px-2 py-1">
                          {cell(rates[o])}
                        </td>
                      )
                    })
                  )}
                  <td className="px-2 py-1">{cell(v.task_success)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {shown['Capability metrics'] && (
        <table className="mb-4 min-w-full border-collapse text-sm">
          <caption className="text-left font-semibold">Capability metrics</caption>
          <thead>
            <tr>
              {[
                'System',
                'Unsafe admitted / proposed',
                'Permitted wrongly blocked',
                'Tool-call accuracy',
                'E2 recovery',
                'Injection compliance',
                'Log-deletion attempts',
                'Latency p50 / p95',
              ].map((h) => (
                <th key={h} scope="col" className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(by).map(([s, v]) => (
              <tr key={s} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left font-mono">
                  {s}
                </th>
                <td className="px-2 py-1">{cell(v.unsafe_proposed_vs_admitted)}</td>
                <td className="px-2 py-1">{cell(v.permitted_wrongly_blocked)}</td>
                <td className="px-2 py-1">{cell(v.tool_call_accuracy)}</td>
                <td className="px-2 py-1">{cell(v.e2_recovery)}</td>
                <td className="px-2 py-1">{cell(v.injection_compliance)}</td>
                <td className="px-2 py-1">{v.log_deletion_attempts}</td>
                <td className="px-2 py-1">
                  {ms(v.latency.p50_ms)} / {ms(v.latency.p95_ms)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
