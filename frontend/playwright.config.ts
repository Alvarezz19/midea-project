import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';

const port = Number(process.env.E2E_PORT ?? 8001);
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${port}`;
const browserExecutablePath = findBrowserExecutablePath();

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 120_000,
  expect: {
    timeout: 20_000
  },
  fullyParallel: false,
  reporter: [['list']],
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    launchOptions: browserExecutablePath ? { executablePath: browserExecutablePath } : undefined,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure'
  },
  webServer: {
    command: `powershell -NoProfile -ExecutionPolicy Bypass -Command "cd ..; conda activate midea; python -m uvicorn app.main:app --host 127.0.0.1 --port ${port}"`,
    url: `${baseURL}/api/health`,
    reuseExistingServer: true,
    timeout: 60_000
  },
  projects: [
    {
      name: browserExecutablePath ? 'local-browser' : 'playwright-chromium'
    }
  ]
});

function findBrowserExecutablePath(): string | undefined {
  const explicitPath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  if (explicitPath && existsSync(explicitPath)) {
    return explicitPath;
  }

  const candidates = [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
  ];
  return candidates.find((candidate) => existsSync(candidate));
}
