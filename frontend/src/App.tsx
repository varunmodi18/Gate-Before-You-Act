import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Route, Routes } from 'react-router'

import { Layout } from './components/Layout'
import { Home } from './pages/Home'
import { NotBuilt } from './pages/NotBuilt'
import { PAGES } from './pages/routes'

export function App({ queryClient }: { queryClient?: QueryClient }) {
  const client = queryClient ?? new QueryClient()
  return (
    <QueryClientProvider client={client}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          {PAGES.filter((p) => p.path !== '/').map((p) => (
            <Route key={p.path} path={`${p.path.slice(1)}/*`} element={<NotBuilt page={p} />} />
          ))}
        </Route>
      </Routes>
    </QueryClientProvider>
  )
}
