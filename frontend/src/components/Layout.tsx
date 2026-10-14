import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router'

import { apiGet, type Health } from '../api/client'
import { PAGES } from '../pages/routes'

export function Layout() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <nav aria-label="Main" className="border-gray-300 bg-gray-100 p-4 md:w-56 md:border-r">
        <p className="mb-4 text-lg font-bold">GateBench</p>
        <ul className="flex flex-wrap gap-1 md:flex-col">
          {PAGES.map((p) => (
            <li key={p.path}>
              <NavLink
                to={p.path}
                end={p.path === '/'}
                className={({ isActive }) =>
                  `block rounded px-2 py-1 ${isActive ? 'bg-blue-800 text-white' : 'text-gray-900 hover:bg-gray-200'}`
                }
              >
                {p.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex flex-1 flex-col">
        <header className="flex items-center gap-6 border-b border-gray-300 px-6 py-3 text-sm">
          <StatusBar />
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

/** Case set and model mode for every page (T8.2): a replay, fake or recording mode is never a
 * research setting, so it is labelled in the header wherever the user is. */
function StatusBar() {
  const health = useQuery({
    queryKey: ['health-header'],
    queryFn: () => apiGet<Health>('/health'),
    refetchInterval: 30_000,
  })
  const h = health.data
  const cs = h?.case_set
  const backend = h?.model?.backend
  const badge = 'rounded px-2 py-0.5 font-semibold'
  return (
    <>
      <span>
        Case set:{' '}
        {cs?.frozen ? (
          <>
            frozen <span className="font-mono">{cs.version}</span>
          </>
        ) : (
          'draft'
        )}
      </span>
      {backend === 'replay' && (
        <span className={`${badge} bg-amber-200 text-amber-950`}>
          REPLAY — recorded demo, never research
        </span>
      )}
      {backend === 'fake' && (
        <span className={`${badge} bg-gray-200 text-gray-900`}>
          FAKE model — test profile, never research
        </span>
      )}
      {backend && backend !== 'replay' && backend !== 'fake' && (
        <span>
          Model: <span className="font-mono">{backend}</span> ({h?.model?.status})
        </span>
      )}
      {h?.model?.recording && (
        <span className={`${badge} bg-red-200 text-red-950`}>RECORDING demo cassette</span>
      )}
    </>
  )
}
