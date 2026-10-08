import * as Tabs from '@radix-ui/react-tabs'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { apiGet, LOG_TABLES, type Cell, type TableRows, type WindowSummary } from '../api/client'
import { DataTable } from '../components/DataTable'
import { ErrorBanner } from '../components/ErrorBanner'
import { RecordDrawer } from '../components/RecordDrawer'
import { SqlConsole } from '../components/SqlConsole'

const PAGE = 50

function TablePanel({
  windowId,
  table,
  onOpenRecord,
}: {
  windowId: string
  table: string
  onOpenRecord: (id: number) => void
}) {
  const [offset, setOffset] = useState(0)
  const [filterCol, setFilterCol] = useState('')
  const [filterText, setFilterText] = useState('')
  const [applied, setApplied] = useState<string[]>([])
  const rows = useQuery({
    queryKey: ['table', windowId, table, offset, applied],
    queryFn: () =>
      apiGet<TableRows>(`/windows/${windowId}/tables/${table}`, {
        limit: PAGE,
        offset,
        filter: applied,
      }),
    placeholderData: keepPreviousData,
  })
  const columns = rows.data?.columns ?? []
  const total = rows.data?.total ?? 0

  return (
    <div>
      <form
        className="mb-2 flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          setOffset(0)
          setApplied(filterCol && filterText ? [`${filterCol}:${filterText}`] : [])
        }}
      >
        <label className="flex flex-col text-sm">
          Filter column
          <select
            className="rounded border border-gray-500 p-1"
            value={filterCol}
            onChange={(e) => setFilterCol(e.target.value)}
          >
            <option value="">(none)</option>
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col text-sm">
          contains
          <input
            className="rounded border border-gray-500 p-1"
            value={filterText}
            onChange={(e) => setFilterText(e.target.value)}
          />
        </label>
        <button type="submit" className="rounded border border-gray-600 px-3 py-1">
          Apply filter
        </button>
      </form>
      {rows.isError && <ErrorBanner error={rows.error} />}
      {rows.isPending && <p>Loading rows…</p>}
      {rows.data && (
        <>
          <p className="mb-2 text-sm" aria-live="polite">
            {total === 0
              ? 'No rows.'
              : `Rows ${offset + 1}–${Math.min(offset + PAGE, total)} of ${total}`}
          </p>
          <DataTable
            caption={`${table} rows`}
            columns={columns}
            rows={rows.data.rows}
            onRowClick={(row: Cell[]) => onOpenRecord(Number(row[0]))}
            rowLabel={(row) => `Open record ${row[0]}`}
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              className="rounded border border-gray-600 px-3 py-1 disabled:opacity-50"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              Previous
            </button>
            <button
              type="button"
              className="rounded border border-gray-600 px-3 py-1 disabled:opacity-50"
              disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export function WindowDetail() {
  const { id = '' } = useParams()
  const [recordId, setRecordId] = useState<number | null>(null)
  const window = useQuery({
    queryKey: ['window', id],
    queryFn: () => apiGet<WindowSummary>(`/windows/${id}`),
  })

  return (
    <section aria-labelledby="window-title">
      <p className="mb-2 text-sm">
        <Link className="text-blue-800 underline" to="/windows">
          ← All windows
        </Link>
      </p>
      {window.isError && <ErrorBanner error={window.error} />}
      {window.data && (
        <>
          <h1 id="window-title" className="text-2xl font-semibold">
            {window.data.title}
          </h1>
          <dl className="mt-2 mb-4 grid grid-cols-[max-content_1fr] gap-x-4 text-sm">
            <dt className="font-semibold">Window</dt>
            <dd className="font-mono">{window.data.id}</dd>
            <dt className="font-semibold">Techniques</dt>
            <dd className="font-mono">{window.data.techniques.join(', ')}</dd>
            <dt className="font-semibold">Tactics</dt>
            <dd>{window.data.tactic_names.join(', ')}</dd>
            <dt className="font-semibold">Hosts</dt>
            <dd className="font-mono">{window.data.hosts.join(', ') || '—'}</dd>
            <dt className="font-semibold">Events</dt>
            <dd>{window.data.event_count ?? '—'}</dd>
            <dt className="font-semibold">Split / status</dt>
            <dd>
              {window.data.split ?? '—'} / {window.data.ingest_status ?? '—'}
            </dd>
          </dl>
          <Tabs.Root defaultValue="process_access">
            <Tabs.List aria-label="Log tables" className="mb-3 flex flex-wrap gap-1">
              {LOG_TABLES.map((t) => (
                <Tabs.Trigger
                  key={t}
                  value={t}
                  className="rounded border border-gray-500 px-2 py-1 text-sm data-[state=active]:bg-blue-800 data-[state=active]:text-white"
                >
                  {t}
                </Tabs.Trigger>
              ))}
            </Tabs.List>
            {LOG_TABLES.map((t) => (
              <Tabs.Content key={t} value={t}>
                <TablePanel windowId={id} table={t} onOpenRecord={setRecordId} />
              </Tabs.Content>
            ))}
          </Tabs.Root>
          <SqlConsole windowId={id} onOpenRecord={setRecordId} />
          <RecordDrawer windowId={id} recordId={recordId} onClose={() => setRecordId(null)} />
        </>
      )}
      {window.isPending && <p>Loading…</p>}
    </section>
  )
}
