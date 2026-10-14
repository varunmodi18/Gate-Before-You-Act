import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// Replay-profile demo (T8.3): the J5 recovery episode served from a recorded cassette, no model.
test('Replay demo: the Console recovery episode from the cassette, labelled REPLAY', async ({
  page,
}) => {
  await page.goto('/console')
  await expect(page.getByText('REPLAY — recorded demo, never research')).toBeVisible()
  await page.getByLabel('Case').selectOption('j3-desktop:E2')
  await page.getByLabel('Gate configuration').selectOption('G3')
  await page.getByRole('button', { name: 'Start' }).click()
  const timeline = page.getByRole('list', { name: 'Episode timeline' })
  await expect(timeline.getByText(/Gate G3: insufficient/)).toBeVisible({ timeout: 30_000 })
  await expect(timeline.getByText(/Gate G3: admitted \(isolate_host\)/)).toBeVisible()
  await expect(page.getByRole('status')).toContainText('Ended: fulfilled')
  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})
