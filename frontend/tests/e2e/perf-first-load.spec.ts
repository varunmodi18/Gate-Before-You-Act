import { expect, test } from '@playwright/test'

// NFR-10 (T8.4): UI first load < 3 s locally — a fresh browser context (no cache) opens the
// built SPA served by the API, until the Home page heading is visible.
test('UI first load under 3 s', async ({ browser }) => {
  const context = await browser.newContext()
  const page = await context.newPage()
  const t0 = Date.now()
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: /^System$/ })).toBeVisible()
  const ms = Date.now() - t0
  console.log(`first load: ${ms} ms`)
  test.info().annotations.push({ type: 'first_load_ms', description: String(ms) })
  expect(ms).toBeLessThan(3000)
  await context.close()
})
