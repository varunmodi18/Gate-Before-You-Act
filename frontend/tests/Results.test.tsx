import { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const run = (id: number, experiment: number, eligible = true) => ({
  id, experiment, purpose: eligible ? 'research' : 'demo', status: 'completed', replay: false,
  research_eligible: eligible, config: {}, config_hash: 'c', case_set_hash: eligible ? 'h' : null,
  git_sha: 'abc', model_id: 'm', model_file_sha256: 'f', backend: 'vllm-awq',
  created_at: '2026-10-14', started_at: null, finished_at: null,
  progress: { done: 1, total: 1, errors: 0, status: 'completed' },
}) // prettier-ignore
const iv = (e: number, lo: number, hi: number) => ({
  estimate: e, ci_low: lo, ci_high: hi, estimate_pp: e * 100, ci_low_pp: lo * 100,
  ci_high_pp: hi * 100, n_scenarios: 40, n_cases: 160,
}) // prettier-ignore
const HYP = {
  units: 'proportion',
  H1: { ...iv(0.18, 0.07, 0.29), target: 0.15, target_pp: 15, target_reached: true,
        statistically_supported: true },
  H1_C4: { ...iv(0.06, -0.03, 0.15), target: 0.1, target_pp: 10, target_reached: false,
           statistically_supported: false },
  H2: { ...iv(0.04, -0.02, 0.11), margin: 0.1, margin_pp: 10, decision: 'inconclusive' },
  exploratory: { code_ticket: { exploratory: true } }, bootstrap: { B: 10000, seed: 2026 },
  provenance: { exp1: { run_id: 1, experiment: 1, git_sha: 'abc', case_set_hash: 'h' },
                exp2: { run_id: 2, experiment: 2, git_sha: 'abc', case_set_hash: 'h' } },
} // prettier-ignore
const RESULTS = {
  run_id: 1, experiment: 1, units: 'proportion', purpose: 'research', replay: false,
  research_eligible: true,
  by_system: { G1: { wrongly_admitted_e2_e5: { total: 64, n: 160, rate: 0.4, rate_pp: 40 },
    wrongly_admitted_by_variant: {}, wrongly_blocked: { refused: 'labels are pending' },
    check_failures: { C2: 3 }, decisions: 200 } },
} // prettier-ignore

describe('Results page (T7.4)', () => {
  it('shows hypothesis cards in percentage points with targets and the H2 decision', async () => {
    let posted: unknown = null
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.endsWith('/analysis/hypotheses')) {
          posted = JSON.parse(String(init?.body))
          return new Response(JSON.stringify(HYP))
        }
        if (url.endsWith('/runs/1/results')) return new Response(JSON.stringify(RESULTS))
        if (url.endsWith('/runs/1')) return new Response(JSON.stringify(run(1, 1)))
        return new Response(JSON.stringify([run(1, 1), run(2, 2), run(3, 2, false)]))
      }),
    )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <MemoryRouter initialEntries={['/results/1']}>
        <App queryClient={client} />
      </MemoryRouter>,
    )
    expect(
      await screen.findByRole('heading', { name: 'Results for run 1: Experiment 1' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('note')).toBeNull() // research run: no illustrative watermark
    const select = await screen.findByLabelText('Experiment 2 research run')
    expect(screen.queryByRole('option', { name: 'run 3' })).toBeNull() // demo run not offered
    fireEvent.change(select, { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: /^Compute/ }))
    await vi.waitFor(() => expect(posted).toEqual({ run_ids: { exp1: 1, exp2: 2 } }))
    const h1 = await screen.findByRole('article', { name: /^H1 — wrongly admitted/ })
    expect(h1).toHaveTextContent('Estimate 18.0 pp (95% CI 7.0 pp to 29.0 pp)')
    expect(h1).toHaveTextContent('Target 15.0 pp: reached; statistically supported: yes')
    expect(screen.getByRole('article', { name: 'H2' })).toHaveTextContent(
      'Margin 10.0 pp: inconclusive',
    )
    expect(screen.getByRole('img', { name: /^Differences with 95% intervals/ })).toBeInTheDocument()
    expect(screen.getByRole('table', { name: /Wrongly admitted/ })).toHaveTextContent(
      '40.0 pp (n 160)',
    )
    expect(screen.getByRole('table', { name: /Wrongly blocked/ })).toHaveTextContent('refused')
  })
})
