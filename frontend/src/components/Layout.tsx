import { NavLink, Outlet } from 'react-router'

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
          <span>Case set: draft</span>
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
