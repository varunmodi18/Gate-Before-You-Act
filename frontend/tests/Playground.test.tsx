import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'

const CASES = [
  {
    id: 'hm:E3',
    scenario_id: 'hm',
    set: 'E',
    variant: 'E3',
    request: { text: 'Contain host WKSTN-01.' },
    package: { tool: 'isolate_host', args: { host: 'WKSTN-01.lab.local' }, cited: [19, 22] },
    expected_label: 'reject',
  },
]
const SYSTEMS = [
  {
    id: 'G1',
    checks: ['C1', 'C5', 'C6'],
    capability: 'policy-only',
    available: true,
    reason: null,
  },
  {
    id: 'A1',
    checks: ['C1', 'C2', 'C3', 'C5', 'C6'],
    capability: 'x',
    available: true,
    reason: null,
  },
  {
    id: 'G3',
    checks: ['C1', 'C2', 'C3', 'C4', 'C5', 'C6'],
    capability: 'proposed',
    available: false,
    reason: 'needs the C4 verifier (milestone M3)',
  },
]
const ok = (check: string, code: string) => ({
  check,
  passed: true,
  code,
  message: '',
  details: {},
  duration_ms: 0,
})
const RESULT = {
  case_id: 'hm:E3',
  expected_label: 'reject',
  decisions: [
    {
      config_id: 'G1',
      verdict: 'admitted',
      admitted: true,
      failed_check: null,
      checks: [ok('C1', 'OK'), ok('C5', 'P2-tier12-isolate'), ok('C6', 'NO_APPROVAL_NEEDED')],
      verifier: null,
      approval: null,
      message: 'Admitted.',
      ms: 1,
      correct: false,
    },
    {
      config_id: 'A1',
      verdict: 'rejected',
      admitted: false,
      failed_check: 'C3',
      checks: [
        ok('C1', 'OK'),
        ok('C2', 'OK'),
        {
          check: 'C3',
          passed: false,
          code: 'C3_HOST_MISMATCH',
          message: 'No cited record is on host WKSTN-01.lab.local',
          details: {},
          duration_ms: 0,
        },
      ],
      verifier: null,
      approval: null,
      message: 'C3_HOST_MISMATCH: No cited record is on host WKSTN-01.lab.local',
      ms: 1,
      correct: true,
    },
  ],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Gate Playground', () => {
  it('runs the selected gates and shows the check pipeline with text, not colour alone', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/playground/cases')) return new Response(JSON.stringify(CASES))
      if (url.endsWith('/playground/systems')) return new Response(JSON.stringify(SYSTEMS))
      if (url.endsWith('/playground/gate')) {
        expect(JSON.parse(String(init?.body))).toEqual({ case_id: 'hm:E3', systems: ['G1', 'A1'] })
        return new Response(JSON.stringify(RESULT))
      }
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/playground']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    await screen.findByRole('option', { name: /hm:E3/ })
    fireEvent.change(screen.getByLabelText('Case'), { target: { value: 'hm:E3' } })
    expect(screen.getByText(/citing \[19, 22\]/)).toBeInTheDocument()
    expect(await screen.findByText(/needs the C4 verifier/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /G3/ })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Run gates' }))
    const table = await screen.findByRole('table', { name: /Gate decisions/ })
    const a1 = within(table).getByRole('row', { name: /A1/ })
    expect(a1).toHaveTextContent('failed: C3_HOST_MISMATCH')
    expect(a1).toHaveTextContent('✓ correct')
    const g1 = within(table).getByRole('row', { name: /G1/ })
    expect(g1).toHaveTextContent('✕ incorrect')
    expect(g1).toHaveTextContent('not run') // C2, C3, C4 are not part of G1
  })
})
