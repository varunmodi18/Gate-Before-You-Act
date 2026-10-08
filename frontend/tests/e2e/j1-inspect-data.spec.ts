import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// J1 Inspect data (plan §E.2): Windows → filter credential_access → open a window →
// run `SELECT * FROM process_access LIMIT 20` → open a record. Uses the fixture window.
const WINDOW_ID = 'SDWIN-MINI-000001'

async function expectNoSeriousA11yViolations(page: Page) {
  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
}

test('J1: inspect data through the Windows page', async ({ page }) => {
  await page.goto('/windows')
  await expect(page.getByRole('heading', { name: 'Windows' })).toBeVisible()
  await expectNoSeriousA11yViolations(page)

  await page.getByLabel('Tactic').selectOption('credential_access')
  const link = page.getByRole('link', { name: WINDOW_ID })
  await expect(link).toBeVisible()
  await expect(page.getByText('SDWIN-000000000002')).toHaveCount(0) // defense evasion, filtered out
  await link.click()

  await expect(page.getByRole('heading', { name: /Mini fixture window/ })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'process_access' })).toHaveAttribute(
    'data-state',
    'active',
  )
  await expect(page.getByText('Rows 1–4 of 4')).toBeVisible()
  await expectNoSeriousA11yViolations(page)

  await page.getByLabel(/Read-only SELECT/).fill('SELECT * FROM process_access LIMIT 20')
  await page.getByRole('button', { name: 'Run query' }).click()
  await expect(page.getByText(/4 rows\. Ran:/)).toBeVisible()

  await page.getByRole('button', { name: 'Open record 5' }).last().click()
  const dialog = page.getByRole('dialog', { name: 'Record 5' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByText('Normalised (process_access)')).toBeVisible()
  await expect(dialog.getByText(/"SourceUser": "LAB\\\\a\.mehta"/)).toBeVisible()
  await expectNoSeriousA11yViolations(page)
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})

test('J1: a rejected query shows the typed error', async ({ page }) => {
  await page.goto(`/windows/${WINDOW_ID}`)
  await page.getByLabel(/Read-only SELECT/).fill('DROP TABLE process_access')
  await page.getByRole('button', { name: 'Run query' }).click()
  const alert = page.getByRole('alert')
  await expect(alert).toContainText('SQL_REJECTED')
  await expect(alert).toContainText('Only SELECT statements are allowed')
})

test('J1: table filter and keyboard navigation', async ({ page }) => {
  await page.goto(`/windows/${WINDOW_ID}`)
  await page.getByRole('tab', { name: 'network' }).click()
  await expect(page.getByText('Rows 1–3 of 3')).toBeVisible()
  await page.getByLabel('Filter column').selectOption('direction')
  await page.getByLabel('contains').fill('inbound')
  await page.getByRole('button', { name: 'Apply filter' }).click()
  await expect(page.getByText('Rows 1–1 of 1')).toBeVisible()
  // tabs are keyboard-reachable (Radix roving focus)
  await page.getByRole('tab', { name: 'network' }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'registry' })).toBeFocused()
})
