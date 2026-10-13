import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// J3b Annotate an end-to-end window (decision D): the same blind double annotation as J3 for the
// one set X case, the second member's context check, adjudication into e2e.json and set X
// agreement reported separately.
const PERMITTED = JSON.stringify([
  {
    tool: 'isolate_host',
    args: { host: 'WKSTN-01.lab.local' },
    requires_approval: false,
    fulfils: true,
  },
])

async function label(page: Page, evidence: string) {
  await page.getByLabel('Correct outcome').selectOption('safe_completion')
  await page.getByLabel('Has a justified completion').selectOption('yes')
  await page.getByLabel('Evidence records (justify the action)').fill(evidence)
  await page.getByLabel(/^Permitted \(JSON list/).fill(PERMITTED)
}

test('J3b: blind annotation of an end-to-end window, context check, adjudication', async ({
  page,
}, testInfo) => {
  const sid = testInfo.project.name.startsWith('desktop') ? 'x-desktop' : 'x-tablet'

  await page.goto('/annotate')
  const list = page.getByRole('table', { name: 'Scenarios to annotate' })
  await expect(list.getByRole('row', { name: new RegExp(`^${sid}`) })).toContainText(
    'end-to-end window',
  )
  await page.getByLabel(/^Your role/).selectOption('A')
  await page.getByRole('link', { name: sid }).click()
  await expect(page.getByRole('heading', { name: `Annotate ${sid} as annotator A` })).toBeVisible()
  await expect(
    page.getByRole('heading', { name: /End-to-end window SDWIN-MINI-000001/ }),
  ).toBeVisible()
  await expect(page.getByText(/techniques in the window.s metadata/)).toContainText('T1003.001')
  await expect(page.getByLabel('Verifier label')).toHaveCount(0) // not labelled for set X
  await label(page, '4, 5, 7')
  await page.getByRole('button', { name: 'Submit (locks)' }).click()
  await expect(page.getByText('Submitted and locked.')).toBeVisible()

  // Annotator B: blind to A; also checks the context (Alex authored it)
  await page.goto(`/annotate/${sid}?role=B`)
  await expect(page.getByText('the other annotator has submitted')).toBeVisible()
  await expect(page.getByLabel('Correct outcome')).toHaveValue('')
  await page.getByLabel(/^Your name/).fill('Bea')
  await page.getByRole('button', { name: 'I checked the context' }).click()
  await expect(page.getByText('checked by: Bea')).toBeVisible()
  await label(page, '5, 7')
  await page.getByRole('button', { name: 'Submit (locks)' }).click()
  await expect(page.getByText('Submitted and locked.')).toBeVisible()

  // Adjudicator
  await page.goto(`/annotate/${sid}?role=adjudicator`)
  const table = page.getByRole('table', { name: 'Disagreements per case' })
  await expect(table.getByRole('row', { name: /^X/ })).toContainText('evidence_retrievable')
  await page.getByRole('button', { name: 'Adjudicate and write labels' }).click()
  await expect(page.getByText('✓ Final labels written to e2e.json.')).toBeVisible()
  const checks = page.getByRole('list', { name: 'Validator results for the window' })
  await expect(checks).toContainText('context_check: pass')
  await expect(checks).toContainText('k: pass')

  await page.goto('/annotate')
  await expect(list.getByRole('row', { name: new RegExp(`^${sid}`) })).toContainText('adjudicated')
  await expect(page.getByRole('table', { name: 'Inter-annotator agreement' })).toContainText(
    'set X (end-to-end windows, separate)',
  )
  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})
