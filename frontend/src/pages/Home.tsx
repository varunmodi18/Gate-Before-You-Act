import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { apiGet, type Health } from '../api/client'

function Card({
  title,
  ok,
  children,
}: {
  title: string
  ok: boolean | null
  children?: ReactNode
}) {
  return (
    <div className="rounded border border-gray-300 p-4">
      <h2 className="font-medium">{title}</h2>
      <p role="status" aria-live="polite" className="mt-1">
        {ok === null ? (
          'Checking…'
        ) : ok ? (
          <span className="text-green-800">✓ OK</span>
        ) : (
          <span className="text-red-800">✕ Not ready</span>
        )}
      </p>
      {children && <div className="mt-1 text-sm">{children}</div>}
    </div>
  )
}

/** Home / System (plan §E.1 row 1): health cards for the API, worker, model, data and index. */
export function Home() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => apiGet<Health>('/health'),
    refetchInterval: 10_000,
  })
  const h = health.data
  const pilot = h?.model?.last_pilot
  return (
    <section aria-labelledby="home-title">
      <h1 id="home-title" className="mb-4 text-2xl font-semibold">
        System
      </h1>
      <div className="grid max-w-4xl grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div className="rounded border border-gray-300 p-4">
          <h2 className="font-medium">API</h2>
          <p role="status" aria-live="polite" className="mt-1">
            {health.isPending && 'Checking…'}
            {health.isError && <span className="text-red-800">✕ Unreachable</span>}
            {h && <span className="text-green-800">{h.api === 'ok' ? '✓ OK' : `✕ ${h.api}`}</span>}
          </p>
        </div>
        {h?.worker && (
          <Card title="Worker" ok={h.worker.alive}>
            {h.worker.last_heartbeat
              ? `last heartbeat ${h.worker.last_heartbeat.slice(0, 19).replace('T', ' ')} UTC`
              : 'no heartbeat yet (make worker)'}
          </Card>
        )}
        {h?.model && (
          <Card title="Model" ok={h.model.status === 'ok'}>
            <p>
              {h.model.backend} · {h.model.status}
            </p>
            <p className="font-mono text-xs break-all">{h.model.model_id}</p>
            {pilot && (
              <p>
                last pilot: {pilot.in_tps} prompt tok/s, {pilot.out_tps} output tok/s, peak VRAM{' '}
                {pilot.peak_vram_mb} MB
              </p>
            )}
          </Card>
        )}
        {h?.data && (
          <Card title="Data" ok={h.data.windows > 0}>
            {h.data.windows} windows catalogued
          </Card>
        )}
        {h?.data && (
          <Card title="Index" ok={h.data.index}>
            {h.data.index ? 'Sigma + ATT&CK index present' : 'missing (make index)'}
          </Card>
        )}
      </div>
    </section>
  )
}
