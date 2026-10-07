import { QueryClient } from '@tanstack/react-query'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App } from '../src/App'
import { PAGES } from '../src/pages/routes'

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

describe('SPA shell', () => {
  it('renders a sidebar link for every page of plan §E.1', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    )
    renderAt('/')
    const nav = screen.getByRole('navigation', { name: 'Main' })
    for (const page of PAGES) {
      expect(nav).toHaveTextContent(page.label)
    }
  })

  it('shows API health from /api/v1/health', async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ api: 'ok' }), { status: 200 }),
    )
    vi.stubGlobal('fetch', fetchMock)
    renderAt('/')
    expect(await screen.findByText('✓ OK')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/health', expect.anything())
  })

  it('shows an unreachable state when the API fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('down', { status: 503 })),
    )
    renderAt('/')
    expect(await screen.findByText('✕ Unreachable')).toBeInTheDocument()
  })

  it('routes unbuilt pages to a labelled placeholder', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    )
    renderAt('/windows')
    expect(screen.getByRole('heading', { name: 'Windows' })).toBeInTheDocument()
    expect(screen.getByText(/task T1\.7/)).toBeInTheDocument()
  })
})
