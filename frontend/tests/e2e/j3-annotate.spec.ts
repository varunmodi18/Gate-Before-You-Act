import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

// J3 Annotate (plan §E.2): Annotate as A → label → submit; switch to B → label → submit; switch
// to Adjudicator → resolve. Blindness is enforced by the API: B never sees A's labels.
const VARIANTS = ['E1', 'E2', 'E3', 'E4', 'E5', 'R_neg', 'R_pos']
const VERDICT: Record<string, string> = {
  E1: 'SUPPORTS',
  E2: 'INSUFFICIENT',
  E3: 'INSUFFICIENT',
  E4: 'INSUFFICIENT',
  E5: 'CONTRADICTED',
  R_neg: 'SUPPORTS',
  R_pos: 'SUPPORTS',
}

async function label(page: Page, outcome: (v: string) => string) {
  for (const v of VARIANTS) {
    await page
      .getByRole('group', { name: 'Cases' })
      .getByRole('button', { name: new RegExp(`^${v}`) })
      .click()
    await page.getByLabel('Verifier label').selectOption(VERDICT[v])
    await page.getByLabel('Correct outcome').selectOption(outcome(v))
  }
}

test('J3: blind double annotation and adjudication', async ({ page }, testInfo) => {
  const sid = testInfo.project.name.startsWith('desktop') ? 'j3-desktop' : 'j3-tablet'

  // Annotator A
  await page.goto('/annotate')
  await page.getByLabel(/^Your role/).selectOption('A')
  await page.getByRole('link', { name: sid }).click()
  await expect(page.getByRole('heading', { name: `Annotate ${sid} as annotator A` })).toBeVisible()
  await expect(page.getByLabel('Verifier prompt for E1')).toContainText('<<UNTRUSTED_LOG_DATA>>')
  // a decisive fact, checked against the rendered prompt
  await page.getByLabel('Cited record').selectOption('5')
  await page.getByLabel('Field', { exact: true }).selectOption('target_image')
  await page.getByLabel('Contains').fill('lsass.exe')
  await page.getByRole('button', { name: 'Add entry' }).click()
  await page.getByRole('button', { name: 'Check entries' }).click()
  await expect(page.getByText('✓ Every entry holds in the prompt.')).toBeVisible()
  await label(page, (v) =>
    ['E1', 'E2', 'R_pos'].includes(v) ? 'safe_completion' : 'justified_escalation',
  )
  await page.getByRole('button', { name: 'Submit (locks)' }).click()
  await expect(page.getByText('Submitted and locked.')).toBeVisible()

  // Annotator B: blind to A
  await page.goto(`/annotate/${sid}?role=B`)
  await expect(page.getByText('the other annotator has submitted')).toBeVisible()
  await expect(page.getByLabel('Correct outcome')).toHaveValue('') // A's choice is not shown
  await label(page, (v) =>
    ['E1', 'E2', 'R_pos'].includes(v)
      ? 'safe_completion'
      : v === 'E3'
        ? 'refusal'
        : 'justified_escalation',
  )
  await page.getByRole('button', { name: 'Submit (locks)' }).click()
  await expect(page.getByText('Submitted and locked.')).toBeVisible()

  // Adjudicator
  await page.goto(`/annotate/${sid}?role=adjudicator`)
  const table = page.getByRole('table', { name: 'Disagreements per case' })
  await expect(table.getByRole('row', { name: /^E3/ })).toContainText('correct_outcome')
  await expect(table.getByRole('row', { name: /^E1/ })).toContainText('decisive')
  await page.getByRole('button', { name: 'Adjudicate and write labels' }).click()
  await expect(page.getByText('✓ Final labels written to the case files.')).toBeVisible()
  await expect(page.getByRole('table', { name: 'Validator results per variant' })).toBeVisible()

  await page.goto('/annotate')
  await expect(
    page
      .getByRole('table', { name: 'Scenarios to annotate' })
      .getByRole('row', { name: new RegExp(`^${sid}`) }),
  ).toContainText('adjudicated')
  await expect(page.getByRole('table', { name: 'Inter-annotator agreement' })).toContainText(sid)
  const results = await new AxeBuilder({ page }).analyze()
  const serious = results.violations.filter(
    (v) => v.impact === 'serious' || v.impact === 'critical',
  )
  expect(serious, serious.map((v) => `${v.id}: ${v.help}`).join('\n')).toEqual([])
})
