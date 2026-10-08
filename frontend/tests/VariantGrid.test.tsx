import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { VariantGrid } from '../src/components/VariantGrid'

afterEach(cleanup)

describe('VariantGrid', () => {
  it('shows each check status in text and lists failures', () => {
    render(
      <VariantGrid
        report={{
          scenario_id: 's1',
          valid: false,
          complete: false,
          tokenizer: 'approx-test-only',
          checks: [
            { check: 'variants', case_id: null, status: 'pass', message: 'all seven cases exist' },
            { check: 'd', case_id: 's1:E1', status: 'pass', message: 'every cited ID exists' },
            { check: 'g', case_id: 's1:E1', status: 'fail', message: 'over the evidence budget' },
            { check: 'h', case_id: 's1:E2', status: 'pending', message: 'labels pending' },
          ],
        }}
      />,
    )
    expect(screen.getByText(/✕ invalid; labels pending/)).toBeInTheDocument()
    const grid = screen.getByRole('table', { name: 'Validator results per variant' })
    const e1 = within(grid).getByRole('row', { name: /^E1/ })
    expect(e1).toHaveTextContent('pass')
    expect(e1).toHaveTextContent('fail')
    expect(within(grid).getByRole('row', { name: /^E2/ })).toHaveTextContent('pending')
    expect(screen.getByText(/\(g\) s1:E1: over the evidence budget/)).toBeInTheDocument()
  })
})
