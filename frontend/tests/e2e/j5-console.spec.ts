import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J5 Watch an agent recover (plan §E.2), Fake-LLM profile: on E2 (benign citations) the scripted
// agent gets INSUFFICIENT from G3, runs one recovery query, re-cites the dumper's records and is
// admitted. The trace page shows the same steps and saves an error tag.
test('J5: watch an agent recover in the Console, then tag its trace', async ({ page }) => {
  await page.goto('/console')
  await expect(page.getByRole('heading', { name: 'Agent Console' })).toBeVisible()
  await expect(page.getByRole('option', { name: /j3-desktop:E2/ })).toBeAttached()
  await page.getByLabel('Case').selectOption('j3-desktop:E2')
  await page.getByLabel('Gate configuration').selectOption('G3')
  await expect(page.getByText(/Request: /)).toContainText('Contain host WKSTN-01.')
  await page.getByRole('button', { name: 'Start' }).click()

  const timeline = page.getByRole('list', { name: 'Episode timeline' })
  await expect(timeline.getByText(/Gate G3: insufficient/)).toBeVisible({ timeout: 30_000 })
  await expect(timeline.getByText('Tool result: sql_query')).toBeVisible()
  await expect(timeline.getByText('untrusted log data').first()).toBeVisible()
  await expect(timeline.getByText(/Gate G3: admitted \(isolate_host\)/)).toBeVisible()
  await expect(page.getByText(/Recovery budget: 1 of 2 queries used/)).toBeVisible()
  const status = page.getByRole('status')
  await expect(status).toContainText('Ended: fulfilled')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeDisabled()

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])

  await status.getByRole('link', { name: 'Open the trace' }).click()
  await expect(page.getByRole('heading', { name: /^Episode \d+$/ })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Tool calls with their classes' })).toContainText(
    'sql_query',
  )
  await page.getByRole('checkbox', { name: 'weak evidence accepted' }).check()
  await page.getByRole('button', { name: 'Save tags' }).click()
  await expect(page.getByText('Saved.')).toBeVisible()
  await page.goto('/traces')
  await expect(page.getByRole('table', { name: 'Episodes' })).toContainText(
    'weak_evidence_accepted',
  )
})
