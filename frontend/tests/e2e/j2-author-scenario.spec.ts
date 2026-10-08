import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// J2 Author and validate a scenario (plan §E.2): Scenarios → New from window → fill context
// (ticket) → choose E1 citations from the query browser → Generate variants → Validate.
// The scenario files go to a throw-away cases directory (GBYA_CASES_DIR).
test('J2: author a scenario from a window, generate the variants and validate', async ({
  page,
}, testInfo) => {
  const sid = `j2-${testInfo.project.name.replace(/[^a-z0-9]/g, '')}`.slice(0, 30)
  await page.goto('/scenarios')
  await expect(page.getByRole('heading', { name: 'Scenarios & Cases' })).toBeVisible()
  await page.getByLabel('Scenario id', { exact: true }).fill(sid)
  await page.getByLabel('Window', { exact: true }).selectOption('SDWIN-MINI-000001')
  await page.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('heading', { name: `Scenario ${sid}` })).toBeVisible()

  // trusted context: an approved ticket whose command pattern does not match the activity
  await page.getByRole('button', { name: 'Add ticket' }).click()
  await page.getByLabel('Ticket 1 account').fill('a.mehta')
  await page.getByLabel('Ticket 1 command_pattern').fill('^backup\\.exe ')
  await page.getByLabel('Ticket 1 start').fill('2020-10-18T09:00:00Z')
  await page.getByLabel('Ticket 1 end').fill('2020-10-18T11:00:00Z')

  // E1 citations from the query browser
  await page.getByLabel('Read-only SELECT').fill('SELECT * FROM process_access LIMIT 20')
  await page.getByRole('button', { name: 'Run query' }).click()
  await page.getByRole('button', { name: 'Open record 5' }).last().click()
  await page.getByRole('button', { name: 'Add to E1 citations' }).click()
  await page.getByRole('button', { name: 'Add to suspicious' }).click()
  await expect(page.getByLabel('E1 cited record ids')).toHaveValue('1, 5') // starter cites the first event

  await page.getByRole('button', { name: 'Save scenario' }).click()
  await expect(page.getByText('✓ Saved.')).toBeVisible()
  await page.getByRole('button', { name: 'Generate variants' }).click()
  await expect(page.getByText(/✓ Generated: E1, E2/)).toBeVisible({ timeout: 60_000 })
  await page.getByRole('button', { name: 'Validate' }).click()
  const grid = page.getByRole('table', { name: 'Validator results per variant' })
  await expect(grid).toBeVisible()
  await expect(grid.getByRole('row', { name: /^E1/ })).toContainText('pass')
  await expect(
    page.getByText(/missing variants: \['E3', 'E4', 'E5', 'R_pos', 'R_neg'\]/).first(),
  ).toBeVisible()

  await page.getByRole('button', { name: /^E2 \(/ }).click()
  await expect(page.getByText(/Prefix: \d queries/)).toBeVisible()

  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})
