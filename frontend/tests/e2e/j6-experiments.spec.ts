import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J6 Run and analyse an experiment (plan §E.2): Experiments → New Exp 1 on the hand-made fixture
// → the worker runs it (Fake-LLM profile) → progress → Exp 1V tables → Results (T7.4). The fixture
// run is not a research run, so the Results page is in illustrative mode: estimates and intervals,
// no verdict cards (those need completed research runs on the frozen set).
test('J6: create an Exp 1 run, follow its progress, read the verifier tables and results', async ({
  page,
}) => {
  await page.goto('/experiments')
  await expect(page.getByRole('heading', { name: 'Experiments', exact: true })).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /rerank/ })).toBeDisabled()
  await expect(page.getByRole('checkbox', { name: /^standard/ })).toBeChecked()
  const form = page.getByRole('form', { name: 'New Experiment 1' })
  await form.getByLabel('Purpose').selectOption('fixture')
  // name the cases: other journeys (J2) may have added scenarios to the fixture database
  await form.getByLabel(/^Case ids/).fill('hm:E1 hm:E3 hm:R_neg')
  await form.getByRole('button', { name: 'Create and start' }).click()

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

  // Results (T7.4): illustrative mode for a fixture run
  await page.getByRole('link', { name: /^Open the results of run/ }).click()
  await expect(
    page.getByRole('heading', { name: /^Results for run \d+: Experiment 1$/ }),
  ).toBeVisible()
  await expect(page.getByRole('note')).toContainText('Illustrative mode — not a research result')
  await expect(page.getByText('Provenance', { exact: true })).toBeVisible()
  await expect(page.getByText(/^H1 \(G1 - G3, E2-E5\): /)).toContainText('pp')
  await expect(
    page.getByRole('img', { name: /^Illustrative differences with 95% intervals/ }),
  ).toBeVisible()
  await expect(page.getByText(/target reached|statistically supported/)).toHaveCount(0)
  const admitted = page.getByRole('table', { name: /Wrongly admitted/ })
  await expect(admitted.getByRole('row', { name: /^G0/ })).toContainText('100.0 pp (n 1)')
  await page.getByRole('checkbox', { name: 'Wrongly admitted' }).uncheck()
  await expect(admitted).toHaveCount(0)
  const axeResults = await new AxeBuilder({ page }).analyze()
  const seriousResults = axeResults.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(seriousResults, seriousResults.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])

  await page.getByRole('link', { name: 'Experiments' }).first().click()
  await expect(page.getByRole('table', { name: 'Experiment runs' })).toContainText('completed')
})
