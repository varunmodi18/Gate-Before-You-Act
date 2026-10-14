import { defineConfig, devices } from '@playwright/test'

// Replay-profile demo run (plan T8.3): a separate fixture directory whose builder records the
// Console demo episode into a cassette (Fake model, the Console's own episode path); the server
// then runs with GBYA_LLM_BACKEND=replay on that cassette, so no model is involved and the header
// shows REPLAY.
const PORT = 8011

export default defineConfig({
  testDir: './tests/e2e-replay',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [
    {
      name: 'desktop-1280',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
  ],
  webServer: {
    command:
      `pnpm build && cd .. && uv run python scripts/e2e_fixture.py data/e2e-replay --record-replay && ` +
      `export GBYA_ENV=test GBYA_APP_DB_PATH=$PWD/data/e2e-replay/app.db GBYA_DATA_DIR=$PWD/data/e2e-replay ` +
      `GBYA_LOG_DIR=$PWD/data/e2e-replay/logs GBYA_LLM_BACKEND=replay ` +
      `GBYA_REPLAY_CASSETTE=$PWD/data/e2e-replay/replay.jsonl ` +
      `GBYA_RERANKER_DIR=$PWD/data/e2e-replay/no-reranker GBYA_CASES_DIR=$PWD/data/e2e-replay/cases && ` +
      `uv run uvicorn gbya.api.main:app --app-dir backend --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
})
