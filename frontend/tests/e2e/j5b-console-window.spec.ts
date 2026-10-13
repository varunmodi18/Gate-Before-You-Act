import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J5b Watch a live investigation (T6.3), Fake-LLM profile: on the mini window's end-to-end case
// the scripted investigator profiles the window, hypothesises T1003.001, queries lsass access and
// the technique verifier accepts records 5 and 7; the response phase then isolates the host from
// the handover and G3 admits it.
test('J5b: investigate a window live in the Console, then respond', async ({ page }) => {
  await page.goto('/console')
  await page.getByRole('radio', { name: 'Live investigation (Exp 3)' }).check()
  await expect(
    page.getByRole('option', { name: /SDWIN-MINI-000001 \(mini-e2e:X\)/ }),
  ).toBeAttached()
  await expect(page.getByRole('option', { name: /j3-desktop:E2/ })).not.toBeAttached()
  await page.getByLabel('Window').selectOption('mini-e2e:X')
  await page.getByLabel('Gate configuration').selectOption('G3')
  await expect(page.getByText(/Request: /)).toContainText('Contain host WKSTN-01.')
  await page.getByRole('button', { name: 'Start' }).click()

  const timeline = page.getByRole('list', { name: 'Episode timeline' })
  await expect(timeline.getByRole('heading', { name: 'Phase 1: investigation' })).toBeVisible({
    timeout: 30_000,
  })
  await expect(timeline.getByText('Investigation: window profile')).toBeVisible()
  await expect(timeline.getByText('Investigation query for T1003.001')).toBeVisible()
  await expect(timeline.getByText('Technique verifier: ACCEPT')).toBeVisible()
  await expect(timeline.getByRole('heading', { name: 'Phase 2: response' })).toBeVisible()
  await expect(timeline.getByText(/Gate G3: admitted \(isolate_host\)/)).toBeVisible()

  const findings = page.getByRole('region', { name: 'Investigation findings' })
  await expect(findings).toContainText('Predicted techniques: T1003.001')
  await expect(findings).toContainText('cited 5, 7')
  const status = page.getByRole('status')
  await expect(status).toContainText('Ended: fulfilled')

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])

  await status.getByRole('link', { name: 'Open the trace' }).click()
  await expect(page.getByRole('heading', { name: /^Episode \d+$/ })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Phase 1: investigation' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Investigation findings' })).toBeVisible()
})
