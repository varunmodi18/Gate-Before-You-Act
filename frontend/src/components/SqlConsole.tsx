import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { apiPost, type Cell, type QueryRows } from '../api/client'
import { DataTable } from './DataTable'
import { ErrorBanner } from './ErrorBanner'

/** Guarded SQL against one window (same guard as the agent's sql_query tool). */
export function SqlConsole({
  windowId,
  onOpenRecord,
}: {
  windowId: string
  onOpenRecord: (recordId: number) => void
}) {
  const [sql, setSql] = useState('SELECT * FROM process_access LIMIT 20')
  const run = useMutation({
    mutationFn: (text: string) => apiPost<QueryRows>(`/windows/${windowId}/query`, { sql: text }),
  })
  const recordCol = run.data?.columns.indexOf('record_id') ?? -1

  return (
    <section aria-labelledby="sql-title" className="mt-6">
      <h2 id="sql-title" className="mb-2 text-lg font-semibold">
        SQL console
      </h2>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          run.mutate(sql)
        }}
      >
        <label htmlFor="sql-input" className="block text-sm">
          Read-only SELECT (max 50 rows, 2 s)
        </label>
        <textarea
          id="sql-input"
          className="mt-1 block w-full rounded border border-gray-500 p-2 font-mono text-sm"
          rows={4}
          value={sql}
          onChange={(e) => setSql(e.target.value)}
        />
        <button
          type="submit"
          className="mt-2 rounded bg-blue-800 px-4 py-1 text-white disabled:opacity-60"
          disabled={run.isPending || !sql.trim()}
        >
          {run.isPending ? 'Running…' : 'Run query'}
        </button>
      </form>
      <div aria-live="polite">
        {run.isError && <ErrorBanner error={run.error} />}
        {run.data && (
          <>
            <p className="my-2 text-sm">
              <span aria-hidden="true">✓ </span>
              {run.data.rows.length} rows. Ran: <code>{run.data.sql}</code>
            </p>
            <DataTable
              caption="Query result"
              columns={run.data.columns}
              rows={run.data.rows}
              onRowClick={
                recordCol >= 0 ? (row: Cell[]) => onOpenRecord(Number(row[recordCol])) : undefined
              }
              rowLabel={(row) => `Open record ${row[recordCol]}`}
            />
          </>
        )}
      </div>
    </section>
  )
}
