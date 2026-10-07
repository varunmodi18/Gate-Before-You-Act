import { useQuery } from '@tanstack/react-query'

import { apiGet, type Health } from '../api/client'

export function Home() {
  const health = useQuery({ queryKey: ['health'], queryFn: () => apiGet<Health>('/health') })

  return (
    <section aria-labelledby="home-title">
      <h1 id="home-title" className="mb-4 text-2xl font-semibold">
        System
      </h1>
      <div className="max-w-sm rounded border border-gray-300 p-4">
        <h2 className="font-medium">API</h2>
        <p role="status" aria-live="polite" className="mt-1">
          {health.isPending && 'Checking…'}
          {health.isError && <span className="text-red-800">✕ Unreachable</span>}
          {health.data && (
            <span className="text-green-800">
              {health.data.api === 'ok' ? '✓ OK' : `✕ ${health.data.api}`}
            </span>
          )}
        </p>
      </div>
    </section>
  )
}
