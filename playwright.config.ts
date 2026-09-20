import { defineConfig } from '@playwright/test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export default defineConfig({
  testDir: './tests/browser',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:8742',
    viewport: { width: 1440, height: 1000 },
    launchOptions: process.env.VIDAC_BROWSER_EXECUTABLE
      ? { executablePath: process.env.VIDAC_BROWSER_EXECUTABLE }
      : {},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: 'uv run vidac serve --port 8742',
    url: 'http://127.0.0.1:8742/api/health',
    reuseExistingServer: false,
    timeout: 30_000,
    env: { VIDAC_WORKSPACE: mkdtempSync(join(tmpdir(), 'vidac-browser-')) },
  },
});
