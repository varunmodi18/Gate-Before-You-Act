import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'

const SPEC = {
  spec: {
    experiment: 1,
    verifier_variants: ['standard', 'rationale', 'none', 'rerank'],
    verifier_runs: 3,
    code_only: ['G0', 'G1', 'G2', 'A1'],
    composed: ['G3', 'A2', 'A3', 'A4', 'A6'],
  },
  unavailable: { rerank: 'needs the CPU reranker (T3.7)' },
  variant_of: { G3: 'standard', A2: 'standard', A3: 'rationale', A4: 'none', A6: 'rerank' },
}
const progress = { run_id: 7, status: 'completed', done: 75, total: 75, errors: 0, running: 0,
  eta_s: null, tps_in: 2986.1, tps_out: 145.1 } // prettier-ignore
const RUN = {
  id: 7,
  experiment: 1,
  purpose: 'fixture',
  status: 'completed',
  replay: false,
  research_eligible: false,
  config: { case_ids: ['hm:E1', 'hm:E3'] },
  config_hash: 'f'.repeat(64),
  case_set_hash: null,
  git_sha: '70db2134bdbc',
  model_id: 'Qwen/Qwen2.5-7B-Instruct-AWQ@b250375',
  model_file_sha256: 'bd4972a9',
  backend: 'vllm',
  created_at: '2026-10-08T12:21:30',
  started_at: '2026-10-08T12:21:31',
  finished_at: '2026-10-08T12:21:47',
  progress,
}
const acc = (n: number, e: number, b: number) => ({
  n,
  exact_correct: Math.round(e * n),
  binary_correct: Math.round(b * n),
  exact_accuracy: e,
  binary_accuracy: b,
})
const EVAL = {
  run_id: 7,
  purpose: 'fixture',
  research_eligible: false,
  diagnostic: {
    standard: {
      ...acc(9, 1, 1),
      parse_errors: 0,
      confusion: {},
      by_case_variant: { E1: acc(3, 1, 1), E3: acc(3, 1, 1) },
    },
  },
  gate_path: {
    G3: {
      packages: 9,
      reached_c4: 6,
      conditional: acc(6, 1, 1),
      by_case_variant: {},
    },
  },
  unlabelled_cases: [],
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('Experiments', () => {
  it('creates an Exp 1 run with the runnable variants and shows its results', async () => {
    let posted: unknown = null
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/runs/exp1-spec')) return new Response(JSON.stringify(SPEC))
        if (url.endsWith('/runs') && init?.method === 'POST') {
          posted = JSON.parse(String(init.body))
          return new Response(JSON.stringify(RUN), { status: 201 })
        }
        if (url.endsWith('/runs')) return new Response(JSON.stringify([]))
        if (url.endsWith('/runs/7')) return new Response(JSON.stringify(RUN))
        if (url.endsWith('/runs/7/verifier-eval')) return new Response(JSON.stringify(EVAL))
        return new Response('{}', { status: 404 })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/experiments']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument()
    const rerank = await screen.findByRole('checkbox', { name: /rerank/ })
    expect(rerank).toBeDisabled()
    expect(screen.getByText(/needs the CPU reranker/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Purpose'), { target: { value: 'fixture' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create and start' }))
    expect(await screen.findByRole('heading', { name: 'Run 7: Experiment 1' })).toBeInTheDocument()
    expect(posted).toEqual({
      experiment: 1,
      purpose: 'fixture',
      case_ids: null,
      config: {
        verifier_variants: ['standard', 'rationale', 'none'],
        composed: ['G3', 'A2', 'A3', 'A4'],
      },
      start: true,
    })
    expect(screen.getByText(/75 of 75 units done/)).toBeInTheDocument()
    expect(screen.getByText(/not a research result/)).toBeInTheDocument()
    const diag = await screen.findByRole('table', { name: /Verifier accuracy/ })
    expect(within(diag).getAllByRole('row', { name: /standard/ })[0]).toHaveTextContent('9')
    const path = screen.getByRole('table', { name: /C4 invocation counts/ })
    expect(within(path).getByRole('row', { name: /G3/ })).toHaveTextContent('96')
  })
})
