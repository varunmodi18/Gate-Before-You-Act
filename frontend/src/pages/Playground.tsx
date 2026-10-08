import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  apiGet,
  apiPost,
  type GateResponse,
  type PlaygroundCase,
  type SystemInfo,
} from '../api/client'
import { CheckPipeline } from '../components/CheckPipeline'
import { ErrorBanner } from '../components/ErrorBanner'
import { VerifierPanel } from '../components/VerifierPanel'

export function Playground() {
  const cases = useQuery({
    queryKey: ['pg-cases'],
    queryFn: () => apiGet<PlaygroundCase[]>('/playground/cases'),
  })
  const systems = useQuery({
    queryKey: ['pg-systems'],
    queryFn: () => apiGet<SystemInfo[]>('/playground/systems'),
  })
  const [caseId, setCaseId] = useState('')
  const [chosen, setChosen] = useState<string[]>(['G1', 'A1'])
  const run = useMutation({
    mutationFn: () =>
      apiPost<GateResponse>('/playground/gate', {
        case_id: caseId,
        // configuration order (G0, G1, …), not click order
        systems: (systems.data ?? []).map((s) => s.id).filter((id) => chosen.includes(id)),
      }),
  })
  const selected = cases.data?.find((c) => c.id === caseId)

  const toggle = (id: string) =>
    setChosen((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))

  return (
    <section aria-labelledby="pg-title">
      <h1 id="pg-title" className="mb-2 text-2xl font-semibold">
        Gate Playground
      </h1>
      <p className="mb-4 text-sm">
        Experiment 1 on one case: every selected gate judges the identical package once, with no
        retries. Configurations with C4 call the verifier on the model server.
      </p>
      {cases.isError && <ErrorBanner error={cases.error} />}
      {systems.isError && <ErrorBanner error={systems.error} />}
      <form
        className="mb-4 flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          run.mutate()
        }}
      >
        <label className="flex max-w-md flex-col text-sm">
          Case
          <select
            className="rounded border border-gray-500 p-1"
            value={caseId}
            onChange={(e) => setCaseId(e.target.value)}
          >
            <option value="">Choose a case…</option>
            {cases.data?.map((c) => (
              <option key={c.id} value={c.id}>
                {c.id} ({c.variant})
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <dl className="grid max-w-3xl grid-cols-[max-content_1fr] gap-x-4 text-sm">
            <dt className="font-semibold">Request</dt>
            <dd>{selected.request.text ?? JSON.stringify(selected.request)}</dd>
            <dt className="font-semibold">Package</dt>
            <dd className="font-mono">
              {selected.package.tool}({JSON.stringify(selected.package.args)}) citing [
              {selected.package.cited.join(', ')}]
            </dd>
            <dt className="font-semibold">Expected</dt>
            <dd>{selected.expected_label ?? '—'}</dd>
          </dl>
        )}
        <fieldset>
          <legend className="text-sm font-semibold">Gate configurations</legend>
          <div className="mt-1 flex flex-wrap gap-4">
            {systems.data?.map((s) => (
              <label key={s.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={chosen.includes(s.id)}
                  disabled={!s.available}
                  onChange={() => toggle(s.id)}
                />
                <span className="font-mono">{s.id}</span>
                <span className="text-gray-700">
                  ({s.checks.join(', ')}){s.available ? '' : ` — ${s.reason}`}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div>
          <button
            type="submit"
            className="rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
            disabled={!caseId || chosen.length === 0 || run.isPending}
          >
            {run.isPending ? 'Running…' : 'Run gates'}
          </button>
        </div>
      </form>
      <div aria-live="polite">
        {run.isError && <ErrorBanner error={run.error} />}
        {run.data && (
          <>
            <p className="mb-2 text-sm">
              Case <span className="font-mono">{run.data.case_id}</span>; expected:{' '}
              <strong>{run.data.expected_label ?? '—'}</strong>; verifier label:{' '}
              <strong>{run.data.verifier_label ?? '—'}</strong>
            </p>
            <CheckPipeline decisions={run.data.decisions} />
            {run.data.decisions.map((d) => (
              <VerifierPanel
                key={d.config_id}
                decision={d}
                verifierLabel={run.data.verifier_label}
              />
            ))}
          </>
        )}
      </div>
    </section>
  )
}
