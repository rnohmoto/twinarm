import { defineConfig, devices } from '@playwright/test'

const HOST = '127.0.0.1'
const PORT = 5173
const baseURL = `http://${HOST}:${PORT}`

export default defineConfig({
  testDir: 'e2e',
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['html', { open: 'never' }], ['list']] : 'list',
  use: { baseURL, trace: 'on-first-retry' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    // The dev server, not `preview`: the MSW worker only starts when
    // import.meta.env.DEV is true, and without it there is no backend at all.
    // --host is explicit because Vite otherwise binds ::1 only, which the
    // health check below (and CI runners without IPv6) cannot reach.
    command: `npm run dev -- --host ${HOST} --port ${PORT} --strictPort`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
  },
})
