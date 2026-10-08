import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { apiGet, apiPost, type ScenarioSummary, type WindowSummary } from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'

/** Scenarios & Cases (plan §E.1 row 3): list and create from a window. */
export function Scenarios() {
  const list = useQuery({
    queryKey: ['scenarios'],
    queryFn: () => apiGet<ScenarioSummary[]>('/scenarios'),
  })
  const windows = useQuery({
    queryKey: ['windows-all'],
    queryFn: () => apiGet<WindowSummary[]>('/windows'),
  })
  const [sid, setSid] = useState('')
  const [windowId, setWindowId] = useState('')
  const navigate = useNavigate()
  const create = useMutation({
    mutationFn: () => apiPost<{ id: string }>('/scenarios', { id: sid, window_id: windowId }),
    onSuccess: (r) => void navigate(`/scenarios/${r.id}`),
  })
  return (
    <section aria-labelledby="sc-title">
      <h1 id="sc-title" className="mb-2 text-2xl font-semibold">
        Scenarios &amp; Cases
      </h1>
      <form
        aria-labelledby="sc-new"
        className="mb-4 flex max-w-2xl flex-wrap items-end gap-3 rounded border border-gray-400 p-3"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <h2 id="sc-new" className="w-full font-semibold">
          New scenario from a window
        </h2>
        <div className="flex flex-col text-sm">
          <label htmlFor="sc-id">Scenario id</label>
          <input
            id="sc-id"
            className="rounded border border-gray-500 p-1 font-mono"
            value={sid}
            onChange={(e) => setSid(e.target.value)}
            placeholder="s001"
          />
        </div>
        <div className="flex flex-col text-sm">
          <label htmlFor="sc-window">Window</label>
          <select
            id="sc-window"
            className="rounded border border-gray-500 p-1"
            value={windowId}
            onChange={(e) => setWindowId(e.target.value)}
          >
            <option value="">Choose a window…</option>
            {windows.data
              ?.filter((w) => w.ingest_status !== null)
              .map((w) => (
                <option key={w.id} value={w.id}>
                  {w.id} ({w.split ?? 'no split'}) {w.title.slice(0, 50)}
                </option>
              ))}
          </select>
        </div>
        <button
          type="submit"
          className="rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
          disabled={!sid || !windowId || create.isPending}
        >
          Create
        </button>
        <div className="w-full" aria-live="polite">
          {create.isError && <ErrorBanner error={create.error} />}
        </div>
      </form>
      {list.isError && <ErrorBanner error={list.error} />}
      {list.data && list.data.length === 0 && <p className="text-sm">No scenarios yet.</p>}
      {list.data && list.data.length > 0 && (
        <table className="min-w-full border-collapse text-sm">
          <caption className="sr-only">Scenarios</caption>
          <thead>
            <tr>
              {['Scenario', 'Window', 'Target', 'Status', 'Cases'].map((h) => (
                <th key={h} scope="col" className="border-b border-gray-400 px-2 py-1 text-left">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {list.data.map((s) => (
              <tr key={s.id} className="border-b border-gray-200">
                <th scope="row" className="px-2 py-1 text-left">
                  <Link className="text-blue-800 underline" to={`/scenarios/${s.id}`}>
                    {s.id}
                  </Link>
                </th>
                <td className="px-2 py-1 font-mono">{s.window_id ?? '—'}</td>
                <td className="px-2 py-1">
                  {s.target_host ?? (s.error ? `invalid: ${s.error}` : '—')}
                </td>
                <td className="px-2 py-1">{s.status}</td>
                <td className="px-2 py-1">{s.cases.join(', ') || 'not generated'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
