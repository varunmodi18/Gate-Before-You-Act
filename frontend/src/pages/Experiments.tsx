import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { apiGet, apiPost, type Exp1SpecOut, type Exp2SpecOut, type RunOut } from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'

const PURPOSES = ['development', 'fixture', 'demo', 'research'] as const

/** Experiments (plan §E.1 row 7), basic form (T3.6, T5.4-T5.5): runs with the budget monitor, and
 * Exp 1 and Exp 2 creation. */
export function Experiments() {
  const runs = useQuery({ queryKey: ['runs'], queryFn: () => apiGet<RunOut[]>('/runs') })
  return (
    <section aria-labelledby="exp-title">
      <h1 id="exp-title" className="mb-2 text-2xl font-semibold">
        Experiments
      </h1>
      <NewExp1 />
      <NewExp2 />
      <h2 className="mt-6 mb-2 text-lg font-semibold">Runs</h2>
      {runs.isError && <ErrorBanner error={runs.error} />}
      {runs.data && runs.data.length === 0 && <p className="text-sm">No runs yet.</p>}
      {runs.data && runs.data.length > 0 && (
        <div className="overflow-x-auto">
          <table className="min-w-full border-collapse text-sm">
            <caption className="sr-only">Experiment runs</caption>
            <thead>
              <tr>
                {[
                  'Run',
                  'Exp',
                  'Purpose',
                  'Status',
                  'Progress',
                  'Errors',
                  'Budget (GPU-h)',
                  'Backend',
                  'Created',
                ].map((h) => (
                  <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id} className="border-b border-gray-200">
                  <th scope="row" className="px-2 py-1 text-left">
                    <Link className="text-blue-800 underline" to={`/experiments/${r.id}`}>
                      Run {r.id}
                    </Link>
                  </th>
                  <td className="px-2 py-1">{r.experiment}</td>
                  <td className="px-2 py-1">{r.purpose}</td>
                  <td className="px-2 py-1 font-mono">{r.status}</td>
                  <td className="px-2 py-1">
                    {r.progress.done}/{r.progress.total}
                  </td>
                  <td className="px-2 py-1">{r.progress.errors}</td>
                  <td className="px-2 py-1">
                    {r.progress.budget ? (
                      <>
                        {r.progress.budget.projected_total_h.toFixed(1)} /{' '}
                        {r.progress.budget.limit_h}
                        {r.progress.budget.over && (
                          <strong className="ml-1 text-red-800">over budget</strong>
                        )}
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="px-2 py-1">{r.backend ?? '—'}</td>
                  <td className="px-2 py-1">{r.created_at.slice(0, 19).replace('T', ' ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function NewExp1() {
  const spec = useQuery({
    queryKey: ['exp1-spec'],
    queryFn: () => apiGet<Exp1SpecOut>('/runs/exp1-spec'),
  })
  const [purpose, setPurpose] = useState<(typeof PURPOSES)[number]>('development')
  const [caseIds, setCaseIds] = useState('')
  // null until the user changes the selection; the default is every variant of
  // experiments/exp1.yaml that can run here.
  const [chosen, setChosen] = useState<string[] | null>(null)
  const variants =
    chosen ??
    (spec.data ? spec.data.spec.verifier_variants.filter((v) => !(v in spec.data.unavailable)) : [])
  const navigate = useNavigate()
  const qc = useQueryClient()

  const create = useMutation({
    mutationFn: () => {
      const s = spec.data?.spec
      const variantOf = spec.data?.variant_of ?? {}
      const composed = (s?.composed ?? []).filter((sid) => variants.includes(variantOf[sid]))
      const ids = caseIds
        .split(/[\s,]+/)
        .map((x) => x.trim())
        .filter(Boolean)
      return apiPost<RunOut>('/runs', {
        experiment: 1,
        purpose,
        case_ids: ids.length ? ids : null,
        config: { verifier_variants: variants, composed },
        start: true,
      })
    },
    onSuccess: (run) => {
      void qc.invalidateQueries({ queryKey: ['runs'] })
      void navigate(`/experiments/${run.id}`)
    },
  })

  return (
    <form
      aria-labelledby="new-exp1"
      className="flex max-w-3xl flex-col gap-3 rounded border border-gray-400 p-3"
      onSubmit={(e) => {
        e.preventDefault()
        create.mutate()
      }}
    >
      <h2 id="new-exp1" className="text-lg font-semibold">
        New Experiment 1
      </h2>
      {spec.isError && <ErrorBanner error={spec.error} />}
      <div className="flex flex-col text-sm">
        <label htmlFor="exp-purpose">Purpose</label>
        <select
          id="exp-purpose"
          aria-describedby="exp-purpose-hint"
          className="max-w-xs rounded border border-gray-500 p-1"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value as (typeof PURPOSES)[number])}
        >
          {PURPOSES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <span id="exp-purpose-hint" className="text-xs text-gray-700">
          Research runs need the frozen case set and the live model.
        </span>
      </div>
      <label className="flex flex-col text-sm">
        Case ids (blank: every case with a database)
        <textarea
          className="rounded border border-gray-500 p-1 font-mono"
          rows={2}
          value={caseIds}
          onChange={(e) => setCaseIds(e.target.value)}
        />
      </label>
      {spec.data && (
        <fieldset>
          <legend className="text-sm font-semibold">
            Verifier variants ({spec.data.spec.verifier_runs} runs each)
          </legend>
          <div className="mt-1 flex flex-wrap gap-4">
            {spec.data.spec.verifier_variants.map((v) => {
              const reason = spec.data.unavailable[v]
              return (
                <label key={v} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={variants.includes(v)}
                    disabled={Boolean(reason)}
                    onChange={() =>
                      setChosen(
                        variants.includes(v) ? variants.filter((x) => x !== v) : [...variants, v],
                      )
                    }
                  />
                  <span className="font-mono">{v}</span>
                  {reason && <span className="text-gray-700">— {reason}</span>}
                </label>
              )
            })}
          </div>
          <p className="mt-1 text-xs text-gray-700">
            Code-only gates: {spec.data.spec.code_only.join(', ')} (one run). Composed gates use the
            selected variants: {spec.data.spec.composed.join(', ')}.
          </p>
        </fieldset>
      )}
      <div>
        <button
          type="submit"
          className="rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
          disabled={!spec.data || create.isPending}
        >
          {create.isPending ? 'Creating…' : 'Create and start'}
        </button>
      </div>
      <div aria-live="polite">{create.isError && <ErrorBanner error={create.error} />}</div>
    </form>
  )
}

/** Exp 2 (T5.4): systems with their run counts from experiments/exp2.yaml (systems not yet
 * defined are shown and cannot be chosen), and the optional fallback F1. */
function NewExp2() {
  const spec = useQuery({
    queryKey: ['exp2-spec'],
    queryFn: () => apiGet<Exp2SpecOut>('/runs/exp2-spec'),
  })
  const [purpose, setPurpose] = useState<(typeof PURPOSES)[number]>('development')
  const [caseIds, setCaseIds] = useState('')
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [fallback, setFallback] = useState<'' | 'F1'>('')
  const all = spec.data ? Object.keys(spec.data.spec.systems) : []
  const systems = chosen ?? all.filter((s) => !spec.data?.undefined.includes(s))
  const navigate = useNavigate()
  const qc = useQueryClient()

  const create = useMutation({
    mutationFn: () => {
      const runs = spec.data?.spec.systems ?? {}
      const ids = caseIds
        .split(/[\s,]+/)
        .map((x) => x.trim())
        .filter(Boolean)
      return apiPost<RunOut>('/runs', {
        experiment: 2,
        purpose,
        case_ids: ids.length ? ids : null,
        config: {
          systems: Object.fromEntries(systems.map((s) => [s, runs[s]])),
          fallback: fallback || null,
        },
        start: true,
      })
    },
    onSuccess: (run) => {
      void qc.invalidateQueries({ queryKey: ['runs'] })
      void navigate(`/experiments/${run.id}`)
    },
  })

  return (
    <form
      aria-labelledby="new-exp2"
      className="mt-4 flex max-w-3xl flex-col gap-3 rounded border border-gray-400 p-3"
      onSubmit={(e) => {
        e.preventDefault()
        create.mutate()
      }}
    >
      <h2 id="new-exp2" className="text-lg font-semibold">
        New Experiment 2
      </h2>
      {spec.isError && <ErrorBanner error={spec.error} />}
      <div className="flex flex-col text-sm">
        <label htmlFor="exp2-purpose">Purpose</label>
        <select
          id="exp2-purpose"
          className="max-w-xs rounded border border-gray-500 p-1"
          value={purpose}
          onChange={(e) => setPurpose(e.target.value as (typeof PURPOSES)[number])}
        >
          {PURPOSES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </div>
      <label className="flex flex-col text-sm">
        Case ids (blank: every case with a database; each needs a generated prefix)
        <textarea
          className="rounded border border-gray-500 p-1 font-mono"
          rows={2}
          value={caseIds}
          onChange={(e) => setCaseIds(e.target.value)}
        />
      </label>
      {spec.data && (
        <fieldset>
          <legend className="text-sm font-semibold">Systems (runs per system)</legend>
          <div className="mt-1 flex flex-wrap gap-4">
            {all.map((s) => {
              const missing = spec.data.undefined.includes(s)
              return (
                <label key={s} className="flex items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={systems.includes(s)}
                    disabled={missing}
                    onChange={() =>
                      setChosen(
                        systems.includes(s) ? systems.filter((x) => x !== s) : [...systems, s],
                      )
                    }
                  />
                  <span className="font-mono">{s}</span> ×{spec.data.spec.systems[s]}
                  {missing && <span className="text-gray-700">— not defined yet</span>}
                </label>
              )
            })}
          </div>
          <p className="mt-1 text-xs text-gray-700">
            Run schedule:{' '}
            {Object.entries(spec.data.spec.schedule)
              .map(([r, v]) => `run ${r} = temperature ${v.temperature}, seed ${v.seed}`)
              .join('; ')}
            . Single-run systems use run 1.
          </p>
        </fieldset>
      )}
      <div className="flex flex-col text-sm">
        <label htmlFor="exp2-fallback">Fallback</label>
        <select
          id="exp2-fallback"
          className="max-w-xs rounded border border-gray-500 p-1"
          value={fallback}
          onChange={(e) => setFallback(e.target.value as '' | 'F1')}
        >
          <option value="">none</option>
          <option value="F1">F1 (experiments/fallbacks.yaml)</option>
        </select>
      </div>
      <div>
        <button
          type="submit"
          className="rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
          disabled={!spec.data || systems.length === 0 || create.isPending}
        >
          {create.isPending ? 'Creating…' : 'Create and start'}
        </button>
      </div>
      <div aria-live="polite">{create.isError && <ErrorBanner error={create.error} />}</div>
    </form>
  )
}
