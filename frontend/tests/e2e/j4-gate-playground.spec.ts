import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J4 Compare gates on one package (plan §E.2), code-only part (T2.7): the wrong-target case
// hm:E3 is admitted by the policy-only gate G1 and rejected by A1's target match (C3).
test('J4 (code-only): compare G1 and A1 on a wrong-target package', async ({ page }) => {
  await page.goto('/playground')
  await expect(page.getByRole('heading', { name: 'Gate Playground' })).toBeVisible()
  await expect(page.getByRole('option', { name: /hm:E3/ })).toBeAttached()
  await page.getByLabel('Case').selectOption('hm:E3')
  await expect(page.getByText(/citing \[19, 22\]/)).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /^A6/ })).toBeDisabled() // reranker: T3.7
  await page.getByRole('button', { name: 'Run gates' }).click()

  const table = page.getByRole('table', { name: /Gate decisions/ })
  const g1 = table.getByRole('row', { name: /^G1/ })
  const a1 = table.getByRole('row', { name: /^A1/ })
  await expect(g1).toContainText('admitted')
  await expect(g1).toContainText('✕ incorrect')
  await expect(a1).toContainText('C3_HOST_MISMATCH')
  await expect(a1).toContainText('✓ correct')

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})

test('J4 (code-only): the tier-0 case is converted to an approval request', async ({ page }) => {
  await page.goto('/playground')
  await expect(page.getByRole('option', { name: /hm:R_neg/ })).toBeAttached()
  await page.getByLabel('Case').selectOption('hm:R_neg')
  await page.getByRole('button', { name: 'Run gates' }).click()
  const a1 = page.getByRole('table', { name: /Gate decisions/ }).getByRole('row', { name: /^A1/ })
  await expect(a1).toContainText('converted_to_approval')
  await expect(a1).toContainText('C6_APPROVAL_REQUIRED')
  await expect(a1).toContainText('No response from approver')
})

// J4 with C4 (T3.6): G3 calls the verifier (Fake-LLM profile: a fixed SUPPORTS that says so).
test('J4: G3 with the verifier panel, references and the exact prompt', async ({ page }) => {
  await page.goto('/playground')
  await expect(page.getByRole('option', { name: /hm:E1/ })).toBeAttached()
  await page.getByLabel('Case').selectOption('hm:E1')
  await page.getByRole('checkbox', { name: /^G3/ }).check()
  await page.getByRole('button', { name: 'Run gates' }).click()

  const table = page.getByRole('table', { name: /Gate decisions/ })
  const g3 = table.getByRole('row', { name: /^G3/ })
  await expect(g3).toContainText('admitted')
  await expect(g3).toContainText('✓ correct')
  const panel = page.getByRole('region', { name: /Verifier for G3/ })
  await expect(panel).toContainText('SUPPORTS')
  await expect(panel).toContainText('Fake LLM backend')
  const refs = panel.getByRole('table', { name: /Retrieved for G3/ })
  await expect(refs.getByRole('row', { name: /Test LSASS Memory Access/ })).toContainText('★ gold')
  await expect(refs.getByRole('row', { name: /Test LSASS Memory Access/ })).toContainText(
    'Test Author',
  )
  await panel.getByText('Exact verifier prompt').click()
  await expect(panel.getByText(/^PROPOSED_ACTION/)).toContainText('<<UNTRUSTED_LOG_DATA>>')

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})
