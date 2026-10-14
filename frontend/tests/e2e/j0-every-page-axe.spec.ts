import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// axe on every page (plan T8.3, §I.3): each page of the navigation renders its heading and has no
// serious or critical violation; the header labels the Fake profile.
const PAGES: [string, RegExp][] = [
  ['/', /^System$/],
  ['/windows', /Windows/],
  ['/scenarios', /Scenarios/],
  ['/annotate', /Annotate/],
  ['/playground', /Gate Playground/],
  ['/console', /Agent Console/],
  ['/experiments', /Experiments/],
  ['/results', /Results/],
  ['/adjudicate', /Adjudication queue/],
  ['/traces', /Traces/],
]

for (const [path, heading] of PAGES) {
  test(`axe: ${path}`, async ({ page }) => {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await expect(page.getByText('FAKE model — test profile, never research')).toBeVisible()
    const results = await new AxeBuilder({ page }).analyze()
    const serious = results.violations.filter(
      (v) => v.impact === 'serious' || v.impact === 'critical',
    )
    expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
  })
}
