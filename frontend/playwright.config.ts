import { defineConfig, devices } from '@playwright/test'

// End-to-end journeys (plan §I.3). The API serves the built SPA against a throw-away fixture
// database (scripts/e2e_fixture.py), so the suite needs no OTRF data, GPU or model server.
const PORT = 8010
const DATA = '../data/e2e'

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop-1280', use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
    { name: 'tablet-768', use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } } },
  ],
  webServer: {
    command:
      `pnpm build && cd .. && uv run python scripts/e2e_fixture.py data/e2e && ` +
      `GBYA_ENV=test GBYA_APP_DB_PATH=$PWD/data/e2e/app.db GBYA_DATA_DIR=$PWD/data/e2e ` +
      `uv run uvicorn gbya.api.main:app --app-dir backend --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
  metadata: { fixture: DATA },
})
