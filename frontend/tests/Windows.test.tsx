import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'

const WINDOW = {
  id: 'SDWIN-201018225619',
  title: 'Lsass Memory Dump via Syscalls',
  techniques: ['T1003.001'],
  tactics: ['TA0006'],
  tactic_names: ['credential_access'],
  hosts: ['WORKSTATION5'],
  event_count: 118,
  split: 'unused',
  ingest_status: 'ingested',
}

const TABLE = {
  columns: ['record_id', 'ts', 'host', 'channel', 'event_id', 'target_image'],
  rows: [[16, '2020-10-18T10:56:07.971', 'WORKSTATION5', 'Sysmon', 10, 'C:\\lsass.exe']],
  total: 1,
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status })
}

function mockApi(overrides: Record<string, (init?: RequestInit) => Response> = {}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    for (const [prefix, handler] of Object.entries(overrides)) {
      if (url.startsWith(prefix)) return handler(init)
    }
    if (url.startsWith('/api/v1/windows?') || url === '/api/v1/windows') return json([WINDOW])
    if (url.startsWith(`/api/v1/windows/${WINDOW.id}/tables/`)) return json(TABLE)
    if (url.startsWith(`/api/v1/windows/${WINDOW.id}/records/16`))
      return json({
        record_id: 16,
        table: 'process_access',
        normalised: { record_id: 16, target_image: 'C:\\lsass.exe' },
        raw: { EventID: 10, TargetImage: 'C:\\lsass.exe', SourceUser: 'THESHIRE\\wardog' },
      })
    if (url.startsWith(`/api/v1/windows/${WINDOW.id}`)) return json(WINDOW)
    return json({ error: { code: 'NOT_FOUND', message: url } }, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App queryClient={queryClient} />
    </MemoryRouter>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Windows page', () => {
  it('lists windows and passes the tactic filter to the API', async () => {
    const fetchMock = mockApi()
    renderAt('/windows')
    expect(await screen.findByText('Lsass Memory Dump via Syscalls')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Tactic'), { target: { value: 'credential_access' } })
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/api/v1/windows?tactic=credential_access',
        expect.anything(),
      ),
    )
    expect(await screen.findByRole('link', { name: WINDOW.id })).toHaveAttribute(
      'href',
      `/windows/${WINDOW.id}`,
    )
  })

  it('shows the window detail with table rows and opens the record drawer', async () => {
    mockApi()
    renderAt(`/windows/${WINDOW.id}`)
    expect(await screen.findByRole('heading', { name: WINDOW.title })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'process_access' })).toHaveAttribute(
      'data-state',
      'active',
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Open record 16' }))
    const dialog = await screen.findByRole('dialog', { name: 'Record 16' })
    expect(await within(dialog).findByText(/THESHIRE\\\\wardog/)).toBeInTheDocument()
    expect(within(dialog).getByText('Normalised (process_access)')).toBeInTheDocument()
  })

  it('runs SQL and shows the error envelope on rejection', async () => {
    mockApi({
      [`/api/v1/windows/${WINDOW.id}/query`]: (init) => {
        const sql = JSON.parse(String(init?.body)).sql as string
        if (sql.startsWith('DROP'))
          return json(
            {
              error: {
                code: 'SQL_REJECTED',
                message: 'Only SELECT statements are allowed',
                hint: 'Use a single SELECT',
              },
            },
            400,
          )
        return json({ columns: TABLE.columns, rows: TABLE.rows, sql: `SELECT * FROM (${sql})` })
      },
    })
    renderAt(`/windows/${WINDOW.id}`)
    const box = await screen.findByLabelText(/Read-only SELECT/)
    fireEvent.click(screen.getByRole('button', { name: 'Run query' }))
    expect(await screen.findByText(/1 rows\. Ran:/)).toBeInTheDocument()
    fireEvent.change(box, { target: { value: 'DROP TABLE process_access' } })
    fireEvent.click(screen.getByRole('button', { name: 'Run query' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('SQL_REJECTED')
    expect(alert).toHaveTextContent('Hint: Use a single SELECT')
  })

  it('shows a typed error when the window does not exist', async () => {
    mockApi()
    renderAt('/windows/SDWIN-NOPE')
    expect(await screen.findByRole('alert')).toHaveTextContent('NOT_FOUND')
  })
})
