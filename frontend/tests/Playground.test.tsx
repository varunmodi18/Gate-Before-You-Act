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
    verifier_variant: null,
    retrieval_mode: null,
    available: true,
    reason: null,
  },
  {
    id: 'A1',
    checks: ['C1', 'C2', 'C3', 'C5', 'C6'],
    capability: 'x',
    verifier_variant: null,
    retrieval_mode: null,
    available: true,
    reason: null,
  },
  {
    id: 'G3',
    checks: ['C1', 'C2', 'C3', 'C4', 'C5', 'C6'],
    capability: 'proposed',
    verifier_variant: 'standard',
    retrieval_mode: 'bm25',
    available: false,
    reason: 'needs the retrieval index (make index)',
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
  verifier_label: 'INSUFFICIENT',
  decisions: [
    {
      config_id: 'G1',
      verdict: 'admitted',
      admitted: true,
      failed_check: null,
      checks: [ok('C1', 'OK'), ok('C5', 'P2-tier12-isolate'), ok('C6', 'NO_APPROVAL_NEEDED')],
      verifier: null,
      verifier_call: null,
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
      verifier_call: null,
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
    expect(await screen.findByText(/needs the retrieval index/)).toBeInTheDocument()
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

  it('shows the verifier panel: verdict, ticket scope, references with gold and author, prompt', async () => {
    const g3 = {
      config_id: 'G3',
      verdict: 'admitted',
      admitted: true,
      failed_check: null,
      checks: [ok('C1', 'OK'), ok('C2', 'OK'), ok('C3', 'OK'), ok('C4', 'OK')],
      verifier: null,
      verifier_call: {
        variant: 'standard',
        output: {
          verdict: 'SUPPORTS',
          unmet_requirement: null,
          ticket_scope: {
            applies: false,
            matches: { host: true, account: false, command: false, time: false },
          },
          reason: 'Records 5 and 7 show dumper.exe opening lsass.',
        },
        error: null,
        raw: {},
        prompt_hash: 'abcdef0123456789',
        manifest: {},
        tokens_in: 2215,
        tokens_out: 83,
        ms: 3059.7,
        messages: [
          { role: 'system', content: 'You are the evidence checker' },
          { role: 'user', content: 'PROPOSED_ACTION ...' },
        ],
        references: [
          {
            kind: 'sigma',
            rank: 1,
            doc_id: 'r1',
            title: 'LSASS Access',
            author: 'Jane Doe',
            uri: 'https://github.com/SigmaHQ/sigma/blob/x/r1.yml',
            technique_id: null,
            bm25: 12.5,
            rerank: null,
            shown: true,
            gold: true,
          },
          {
            kind: 'attack',
            rank: 1,
            doc_id: 'a1',
            title: 'LSASS Memory',
            author: null,
            uri: null,
            technique_id: 'T1003.001',
            bm25: 9.1,
            rerank: null,
            shown: true,
            gold: false,
          },
        ],
      },
      approval: null,
      message: 'Admitted.',
      ms: 3100,
      correct: true,
    }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/playground/cases')) return new Response(JSON.stringify(CASES))
        if (url.endsWith('/playground/systems'))
          return new Response(JSON.stringify(SYSTEMS.map((x) => ({ ...x, available: true }))))
        if (url.endsWith('/playground/gate'))
          return new Response(
            JSON.stringify({ ...RESULT, verifier_label: 'SUPPORTS', decisions: [g3] }),
          )
        return new Response('{}', { status: 404 })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/playground']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    await screen.findByRole('option', { name: /hm:E3/ })
    fireEvent.change(screen.getByLabelText('Case'), { target: { value: 'hm:E3' } })
    fireEvent.click(await screen.findByRole('checkbox', { name: /^G3/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Run gates' }))
    const panel = await screen.findByRole('region', { name: /Verifier for G3/ })
    expect(panel).toHaveTextContent('SUPPORTS')
    expect(panel).toHaveTextContent('✓ matches')
    expect(panel).toHaveTextContent('host: ✓ match')
    expect(panel).toHaveTextContent('account: ✕ no match')
    const refs = within(panel).getByRole('table', { name: /Retrieved for G3/ })
    const rule = within(refs).getByRole('row', { name: /LSASS Access/ })
    expect(rule).toHaveTextContent('★ gold')
    expect(rule).toHaveTextContent('Jane Doe')
    expect(within(rule).getByRole('link', { name: 'LSASS Access' })).toHaveAttribute(
      'href',
      'https://github.com/SigmaHQ/sigma/blob/x/r1.yml',
    )
    expect(within(panel).getByText('Exact verifier prompt')).toBeInTheDocument()
    expect(within(panel).getByText('PROPOSED_ACTION ...')).toBeInTheDocument()
  })
})
