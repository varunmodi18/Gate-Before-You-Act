import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { StepOut } from '../src/api/client'
import { App } from '../src/App'
import { recoveryState } from '../src/components/recovery'
import { Timeline } from '../src/components/Timeline'

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

describe('Exp 3 timeline (T6.3)', () => {
  const inv = (idx: number, kind: 'llm' | 'tool', payload: Record<string, unknown>): StepOut => ({
    idx,
    kind,
    payload: { phase: 'investigation', ...payload },
  })
  it('shows the investigation phase before the response', () => {
    const steps: StepOut[] = [
      inv(0, 'tool', { tool: 'profile', content: 'counts: process_access 4' }),
      inv(1, 'llm', { step: 'hypothesise', output: { thought: 't', hypotheses: [
        { technique_id: 'T1003.001', behaviour: 'opens lsass' }] } }), // prettier-ignore
      inv(2, 'tool', { tool: 'sql_query', technique_id: 'T1003.001', ok: true,
        args: { sql: 'SELECT 1' }, content: 'record_id 5' }), // prettier-ignore
      inv(3, 'llm', { step: 'technique_verifier',
        output: { verdict: 'ACCEPT', cited: [5, 7], reason: 'dumper opens lsass' } }), // prettier-ignore
      gate(4, null),
    ]
    render(<Timeline steps={steps} />)
    const items = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(items).toEqual(['Phase 1: investigation', 'Phase 2: response'])
    expect(screen.getByText('Investigation: window profile')).toBeInTheDocument()
    expect(screen.getByText('Investigation query for T1003.001')).toBeInTheDocument()
    expect(screen.getByText('Technique verifier: ACCEPT')).toBeInTheDocument()
    expect(screen.getByText('cited [5, 7]')).toBeInTheDocument()
    expect(screen.getAllByText('untrusted log data')).toHaveLength(2)
  })
  it('has no phase headings for an Exp 2 episode', () => {
    render(<Timeline steps={[sql(0), gate(1, null)]} />)
    expect(screen.queryByRole('heading', { level: 3 })).toBeNull()
  })
})

describe('Adjudication queue: incidental techniques (A-15)', () => {
  it('posts an incidental decision', async () => {
    let posted: unknown = null
    const item = { ref_id: 'w:X::technique:T1059.001', case_id: 'w:X', kind: 'technique',
      tool: 'T1059.001', item: "predicted technique not in the window's metadata", episodes: 1 } // prettier-ignore
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).endsWith('/adjudications') && init?.method === 'POST') {
          posted = JSON.parse(String(init.body))
          return new Response(JSON.stringify({ rescored: 0, queue: 0 }))
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
    fireEvent.click(await screen.findByRole('button', { name: 'Not incidental' }))
    await vi.waitFor(() => expect(posted).toEqual({ ref_id: item.ref_id, incidental: false }))
    expect(screen.queryByRole('button', { name: 'Qualifying' })).toBeNull()
  })
})
