import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { apiGet, apiPost, type AdjudicationItem } from '../api/client'
import { ErrorBanner } from '../components/ErrorBanner'

/** Adjudication queue (plan §E.1 row 9): unlisted calls and escalations of Exp 2/3 episodes and
 * Exp 3 predicted techniques outside the window's metadata (A-15), one per (case, item), with the
 * system hidden. A decision re-scores every episode of the case. */
export function Adjudicate() {
  const queue = useQuery({
    queryKey: ['adjudications'],
    queryFn: () => apiGet<{ count: number; items: AdjudicationItem[] }>('/adjudications'),
  })
  return (
    <section aria-labelledby="adj-title">
      <h1 id="adj-title" className="mb-2 text-2xl font-semibold">
        Adjudication queue
      </h1>
      <p className="mb-3 text-sm">
        Analysis is refused while this queue is not empty. The system that produced an item is not
        shown.
      </p>
      {queue.isError && <ErrorBanner error={queue.error} />}
      {queue.data && (
        <p className="mb-2 text-sm" aria-live="polite">
          {queue.data.count} item{queue.data.count === 1 ? '' : 's'} waiting.
        </p>
      )}
      <ul className="flex flex-col gap-2">
        {(queue.data?.items ?? []).map((it) => (
          <Item key={it.ref_id} item={it} />
        ))}
      </ul>
    </section>
  )
}

function Item({ item }: { item: AdjudicationItem }) {
  const qc = useQueryClient()
  const [fulfils, setFulfils] = useState(false)
  const [needsApproval, setNeedsApproval] = useState(false)
  const decide = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      apiPost('/adjudications', { ref_id: item.ref_id, ...body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['adjudications'] }),
  })
  const btn = 'rounded border border-gray-700 px-3 py-1 text-sm'
  return (
    <li className="rounded border border-gray-400 p-2 text-sm">
      <p>
        <span className="font-mono">{item.case_id}</span> · {item.kind} ·{' '}
        <span className="font-mono">{item.tool}</span>{' '}
        <span className="font-mono text-xs break-all">{item.item}</span> · {item.episodes} episode
        {item.episodes === 1 ? '' : 's'} waiting
      </p>
      {item.kind === 'call' ? (
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={fulfils}
              onChange={(e) => setFulfils(e.target.checked)}
            />
            fulfils the request
          </label>
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={needsApproval}
              onChange={(e) => setNeedsApproval(e.target.checked)}
            />
            requires approval
          </label>
          <button
            type="button"
            className={btn}
            onClick={() =>
              decide.mutate({ label: 'permitted', fulfils, requires_approval: needsApproval })
            }
          >
            Permitted
          </button>
          <button
            type="button"
            className={btn}
            onClick={() => decide.mutate({ label: 'prohibited' })}
          >
            Prohibited
          </button>
        </div>
      ) : item.kind === 'technique' ? (
        <div className="mt-1 flex gap-2">
          <button type="button" className={btn} onClick={() => decide.mutate({ incidental: true })}>
            Incidental (in the window)
          </button>
          <button
            type="button"
            className={btn}
            onClick={() => decide.mutate({ incidental: false })}
          >
            Not incidental
          </button>
        </div>
      ) : (
        <div className="mt-1 flex gap-2">
          <button type="button" className={btn} onClick={() => decide.mutate({ qualifying: true })}>
            Qualifying
          </button>
          <button
            type="button"
            className={btn}
            onClick={() => decide.mutate({ qualifying: false })}
          >
            Not qualifying
          </button>
        </div>
      )}
      {decide.isError && <ErrorBanner error={decide.error} />}
    </li>
  )
}
