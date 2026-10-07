import type { PageDef } from './routes'

/** Scaffold placeholder for a page whose task has not been implemented yet. */
export function NotBuilt({ page }: { page: PageDef }) {
  return (
    <section aria-labelledby="page-title">
      <h1 id="page-title" className="mb-2 text-2xl font-semibold">
        {page.label}
      </h1>
      <p>Not built yet. This page is delivered by task {page.task}.</p>
    </section>
  )
}
