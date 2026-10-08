import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'

import { apiGet, TACTICS, type WindowSummary } from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'

export function WindowsList() {
  const [params, setParams] = useSearchParams()
  const tactic = params.get('tactic') ?? ''
  const split = params.get('split') ?? ''
  const q = params.get('q') ?? ''
  const windows = useQuery({
    queryKey: ['windows', tactic, split, q],
    queryFn: () => apiGet<WindowSummary[]>('/windows', { tactic, split, q }),
  })

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  return (
    <section aria-labelledby="windows-title">
      <h1 id="windows-title" className="mb-4 text-2xl font-semibold">
        Windows
      </h1>
      <form className="mb-4 flex flex-wrap gap-4" onSubmit={(e) => e.preventDefault()}>
        <label className="flex flex-col text-sm">
          Tactic
          <select
            className="rounded border border-gray-500 p-1"
            value={tactic}
            onChange={(e) => setParam('tactic', e.target.value)}
          >
            <option value="">All</option>
            {TACTICS.map((t) => (
              <option key={t.id} value={t.name}>
                {t.name} ({t.id})
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Split
          <select
            className="rounded border border-gray-500 p-1"
            value={split}
            onChange={(e) => setParam('split', e.target.value)}
          >
            <option value="">All</option>
            {['dev', 'test', 'e2e', 'unused'].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          Search (title, id, technique)
          <input
            type="search"
            className="rounded border border-gray-500 p-1"
            value={q}
            onChange={(e) => setParam('q', e.target.value)}
          />
        </label>
      </form>

      {windows.isPending && <p>Loading windows…</p>}
      {windows.isError && <ErrorBanner error={windows.error} />}
      {windows.data && windows.data.length === 0 && (
        <p>No windows match. If none are ingested, run `make data`.</p>
      )}
      {windows.data && windows.data.length > 0 && (
        <div className="overflow-x-auto">
          <p className="mb-2 text-sm" aria-live="polite">
            {windows.data.length} windows
          </p>
          <table className="min-w-full border-collapse text-sm">
            <caption className="sr-only">Catalogued windows</caption>
            <thead>
              <tr>
                {['Window', 'Title', 'Techniques', 'Tactics', 'Events', 'Split', 'Status'].map(
                  (h) => (
                    <th
                      key={h}
                      scope="col"
                      className="border-b border-gray-400 px-2 py-1 text-left font-semibold"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {windows.data.map((w) => (
                <tr key={w.id} className="border-b border-gray-200 align-top">
                  <td className="px-2 py-1 font-mono">
                    <Link className="text-blue-800 underline" to={`/windows/${w.id}`}>
                      {w.id}
                    </Link>
                  </td>
                  <td className="px-2 py-1">{w.title}</td>
                  <td className="px-2 py-1 font-mono">{w.techniques.join(', ')}</td>
                  <td className="px-2 py-1">{w.tactic_names.join(', ')}</td>
                  <td className="px-2 py-1 text-right">{w.event_count ?? '—'}</td>
                  <td className="px-2 py-1">{w.split ?? '—'}</td>
                  <td className="px-2 py-1">{w.ingest_status ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
