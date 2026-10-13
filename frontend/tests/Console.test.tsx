import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { StepOut } from '../src/api/client'
import { App } from '../src/App'
import { recoveryState } from '../src/components/recovery'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const gate = (idx: number, code: string | null, left = 2): StepOut => ({
  idx,
  kind: 'gate',
  payload: {
    decision: {
      verdict: code ? 'insufficient' : 'admitted',
      checks: code
        ? [{ check: 'C4', passed: false, code, details: { recovery_queries_left: left } }]
        : [],
    },
  },
})
const sql = (idx: number, refused = false): StepOut => ({
  idx,
  kind: 'tool',
  payload: {
    tool: 'sql_query',
    ok: !refused,
    error_code: refused ? 'RECOVERY_BUDGET_EXHAUSTED' : null,
  },
})

describe('recovery meter', () => {
  it('counts recovery queries only after the first INSUFFICIENT', () => {
    expect(recoveryState([sql(0), gate(1, null)])).toEqual({ granted: null, used: 0, refused: 0 })
    const steps = [sql(0), gate(1, 'C4_INSUFFICIENT', 1), sql(2), sql(3, true)]
    expect(recoveryState(steps)).toEqual({ granted: 1, used: 1, refused: 1 })
  })
})

describe('Adjudication queue', () => {
  it('posts a call decision for an item whose system is not shown', async () => {
    let posted: unknown = null
    const item = { ref_id: 'mini:E1::isolate_host:{"host": "hr001.lab.local"}', case_id: 'mini:E1',
      kind: 'call', tool: 'isolate_host', item: '{"host": "hr001.lab.local"}', episodes: 2 } // prettier-ignore
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith('/adjudications') && init?.method === 'POST') {
          posted = JSON.parse(String(init.body))
          return new Response(JSON.stringify({ rescored: 2, queue: 0 }))
        }
        return new Response(JSON.stringify({ count: 1, items: [item] }))
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/adjudicate']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('1 item waiting.')).toBeInTheDocument()
    const li = screen.getByText(/2 episodes waiting/).closest('li') as HTMLElement
    expect(li).toHaveTextContent('isolate_host')
    fireEvent.click(within(li).getByRole('checkbox', { name: 'fulfils the request' }))
    fireEvent.click(within(li).getByRole('button', { name: 'Permitted' }))
    await vi.waitFor(
      () =>
      expect(posted).toEqual({ ref_id: item.ref_id, label: 'permitted', fulfils: true,
        requires_approval: false }), // prettier-ignore
    )
  })
})
