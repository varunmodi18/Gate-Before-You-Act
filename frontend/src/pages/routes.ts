// Navigation for the pages of plan §E.1. `task` is the task that builds the page.

export interface PageDef {
  path: string
  label: string
  task: string
}

export const PAGES: PageDef[] = [
  { path: '/', label: 'Home', task: 'T0.1' },
  { path: '/windows', label: 'Windows', task: 'T1.7' },
  { path: '/scenarios', label: 'Scenarios & Cases', task: 'T4.5' },
  { path: '/annotate', label: 'Annotate', task: 'T4.7' },
  { path: '/playground', label: 'Gate Playground', task: 'T2.7' },
  { path: '/console', label: 'Agent Console', task: 'T5.6' },
  { path: '/experiments', label: 'Experiments', task: 'T3.6' },
  { path: '/results', label: 'Results', task: 'T7.4' },
  { path: '/adjudicate', label: 'Adjudication queue', task: 'T5.4' },
  { path: '/traces', label: 'Traces', task: 'T5.6' },
]
