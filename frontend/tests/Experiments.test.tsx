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
  unavailable: { rerank: 'needs the reranker model in models/bge-reranker-base' },
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

const summary = (recall: number) => ({ cases: 2, sigma_n: 2, excluded_empty_gold: 0,
  recall_at_5: recall, hit_at_5: 1, ndcg_at_5: 0.9, mrr_at_20: 1, mean_gold_size: 71,
  mean_recall_ceiling: 0.07, attack_n: 2, attack_top1: 1 }) // prettier-ignore
const RETRIEVAL = {
  run_id: 7,
  index_sha256: 'x',
  rerankers: ['BAAI/bge-reranker-base@2cfc18c'],
  missing: [],
  modes: {
    bm25: { overall: summary(0.0423), by_case_variant: {} },
    bm25_rerank: { overall: summary(0.0704), by_case_variant: {} },
  },
  note: 'Technique tags are proxy relevance labels.',
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
        if (url.endsWith('/runs/7/retrieval')) return new Response(JSON.stringify(RETRIEVAL))
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
    expect(screen.getByText(/needs the reranker model/)).toBeInTheDocument()
    const form = within(screen.getByRole('form', { name: 'New Experiment 1' }))
    fireEvent.change(form.getByLabelText('Purpose'), { target: { value: 'fixture' } })
    fireEvent.click(form.getByRole('button', { name: 'Create and start' }))
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
    const levels = await screen.findByRole('table', { name: /per retrieval level/ })
    expect(within(levels).getByRole('row', { name: /No RAG \(A4\)/ })).toHaveTextContent('n/a')
    expect(within(levels).getByRole('row', { name: /BM25 \(G3\)/ })).toHaveTextContent('0.042')
    expect(within(levels).getByRole('row', { name: /rerank \(A6\)/ })).toHaveTextContent('0.070')
  })

  it('creates an Exp 2 run, shows outcomes and the budget warning with the F1 offer', async () => {
    let posted: unknown = null
    const SPEC2 = {
      spec: {
        experiment: 2,
        schedule: { '1': { temperature: 0.2, seed: 11 }, '2': { temperature: 0.5, seed: 22 } },
        systems: { G0: 2, G3: 2, A7: 1 },
        fallback: null,
      },
      undefined: ['A7'],
    }
    const budget = { limit_h: 81, elapsed_h: 30, run_h: 90, spent_other_research_h: 2,
      projected_total_h: 92, over: true, offer: { fallback: 'F1', drop_systems: ['G2', 'A1'],
        runs: { G0: 2, G3: 2 }, units: 40, run_h: 60, projected_total_h: 62 } } // prettier-ignore
    const RUN2 = { ...RUN, id: 8, experiment: 2, progress: { ...progress, run_id: 8, budget } }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/runs/exp1-spec')) return new Response(JSON.stringify(SPEC))
        if (url.endsWith('/runs/exp2-spec')) return new Response(JSON.stringify(SPEC2))
        if (url.endsWith('/runs') && init?.method === 'POST') {
          posted = JSON.parse(String(init.body))
          return new Response(JSON.stringify(RUN2), { status: 201 })
        }
        if (url.endsWith('/runs')) return new Response(JSON.stringify([RUN2]))
        if (url.endsWith('/runs/8')) return new Response(JSON.stringify(RUN2))
        if (url.endsWith('/runs/8/outcomes'))
          return new Response(
            JSON.stringify({ run_id: 8, by_system: { G3: { safe_completion: 1, unscored: 6 } } }),
          )
        return new Response('{}', { status: 404 })
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/experiments']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    const runs = await screen.findByRole('table', { name: 'Experiment runs' })
    expect(within(runs).getByRole('row', { name: /Run 8/ })).toHaveTextContent('over budget')
    const form = within(screen.getByRole('form', { name: 'New Experiment 2' }))
    expect(await form.findByRole('checkbox', { name: /A7/ })).toBeDisabled()
    fireEvent.change(form.getByLabelText('Fallback'), { target: { value: 'F1' } })
    fireEvent.click(form.getByRole('button', { name: 'Create and start' }))
    expect(await screen.findByRole('heading', { name: 'Run 8: Experiment 2' })).toBeInTheDocument()
    expect(posted).toEqual({
      experiment: 2,
      purpose: 'development',
      case_ids: null,
      config: { systems: { G0: 2, G3: 2 }, fallback: 'F1' },
      start: true,
    })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Projected total exceeds the budget')
    expect(alert).toHaveTextContent('Fallback F1')
    expect(alert).toHaveTextContent('team decision')
    expect(screen.getByText(/^Budget:/)).toHaveTextContent(
      'projected total 92.0 h of 81.0 GPU-hours',
    )
    const out = await screen.findByRole('table', { name: /outcomes per system/ })
    expect(within(out).getByRole('row', { name: /G3/ })).toHaveTextContent('6')
  })
})
