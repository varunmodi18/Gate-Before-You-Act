import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J6 Run and analyse an experiment (plan §E.2), partial (T3.6): Experiments → New Exp 1 on the
// hand-made fixture → the worker runs it (Fake-LLM profile) → progress → Exp 1V tables.
// Results and the H1 card arrive with M7.
test('J6 (partial): create an Exp 1 run, follow its progress, read the verifier tables', async ({
  page,
}) => {
  await page.goto('/experiments')
  await expect(page.getByRole('heading', { name: 'Experiments', exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /rerank/ })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: /^standard/ })).toBeChecked()
  await page.getByLabel('Purpose').selectOption('fixture')
  // name the cases: other journeys (J2) may have added scenarios to the fixture database
  await page.getByLabel(/^Case ids/).fill('hm:E1 hm:E3 hm:R_neg')
  await page.getByRole('button', { name: 'Create and start' }).click()

  await expect(page.getByRole('heading', { name: /^Run \d+: Experiment 1$/ })).toBeVisible()
  await expect(page.getByText(/Status: completed/)).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/75 of 75 units done; 0 errors/)).toBeVisible()
  await expect(page.getByText('fixture (not a research result)')).toBeVisible()
  const diag = page.getByRole('table', { name: /Verifier accuracy/ })
  await expect(diag.getByRole('row', { name: /standard/ }).first()).toContainText('9')
  const path = page.getByRole('table', { name: /C4 invocation counts/ })
  await expect(path.getByRole('row', { name: /^G3/ })).toContainText('6') // hm:E3 stops at C3

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])

  await page.getByRole('link', { name: 'Experiments' }).first().click()
  await expect(page.getByRole('table', { name: 'Experiment runs' })).toContainText('completed')
})
