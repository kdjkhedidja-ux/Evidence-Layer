import { defineConfig, devices } from "@playwright/test";

const appUrl = "http://localhost:3000";
const authUrl = "http://127.0.0.1:54321";
// One issue time for both mock servers so their synthetic tokens match.
const syntheticTokenIssuedAt = String(Math.floor(Date.now() / 1000));
const syntheticConfig = {
  NEXT_PUBLIC_SUPABASE_URL: authUrl,
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_synthetic_ui_only",
  EVIDENCE_SERVICE_URL: "http://127.0.0.1:54329",
  PORT: "3000",
};

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: appUrl,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "node tests/e2e/mock-auth-server.mjs",
      url: `${authUrl}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
      env: { SYNTHETIC_TOKEN_ISSUED_AT: syntheticTokenIssuedAt },
    },
    {
      command: "node tests/e2e/mock-evidence-service.mjs",
      url: "http://127.0.0.1:54329/health",
      reuseExistingServer: false,
      timeout: 30_000,
      env: { SYNTHETIC_TOKEN_ISSUED_AT: syntheticTokenIssuedAt },
    },
    {
      command: "npm run build && npm run start -- --hostname 127.0.0.1 --port 3000",
      url: appUrl,
      reuseExistingServer: false,
      timeout: 180_000,
      env: syntheticConfig,
    },
  ],
});
