import { existsSync, readFileSync } from "node:fs";
import { defineConfig } from "@playwright/test";

const configuredAppUrl = process.env.STAGING_APP_URL;
let appUrl: string | undefined;

if (configuredAppUrl) {
  let parsed: URL;
  try {
    parsed = new URL(configuredAppUrl);
  } catch {
    throw new Error("STAGING_APP_URL must be a valid HTTPS origin.");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    !parsed.hostname.endsWith(".vercel.app") ||
    !parsed.hostname.includes("-git-fix-es256-p256-jwk-test-fixture-")
  ) {
    throw new Error("STAGING_APP_URL must be the HTTPS Vercel branch-preview origin for fix/es256-p256-jwk-test-fixture.");
  }
  appUrl = parsed.origin;
}

const storageState = process.env.STAGING_VERCEL_STORAGE_STATE;
if (storageState && !existsSync(storageState)) {
  throw new Error("STAGING_VERCEL_STORAGE_STATE must point to an existing protected storage-state file.");
}
if (storageState) {
  try {
    const state = JSON.parse(readFileSync(storageState, "utf8")) as {
      cookies?: Array<{ name?: unknown }>;
      origins?: Array<{ localStorage?: Array<{ name?: unknown }> }>;
    };
    const hasSupabaseCookie = state.cookies?.some(
      (cookie) => typeof cookie.name === "string" && /^sb-.+-auth-token(?:\.|$)/.test(cookie.name),
    );
    const hasSupabaseLocalSession = state.origins?.some((origin) =>
      origin.localStorage?.some(
        (item) => typeof item.name === "string" && /^sb-.+-auth-token(?:\.|$)/.test(item.name),
      ),
    );
    const hasProtectionBypass = state.cookies?.some(
      (cookie) =>
        typeof cookie.name === "string" &&
        /(?:vercel.*bypass|protection.*bypass|bypass.*protection)/i.test(cookie.name),
    ) || state.origins?.some((origin) =>
      origin.localStorage?.some(
        (item) =>
          typeof item.name === "string" &&
          /(?:vercel.*bypass|protection.*bypass|bypass.*protection)/i.test(item.name),
      ),
    );
    if (hasSupabaseCookie || hasSupabaseLocalSession || hasProtectionBypass) {
      throw new Error();
    }
  } catch {
    throw new Error("STAGING_VERCEL_STORAGE_STATE must be valid and contain no Supabase Auth session.");
  }
}

const requiredStagingVariables = [
  "STAGING_APP_URL",
  "STAGING_VERCEL_STORAGE_STATE",
  "STAGING_TEST_EMAIL_A",
  "STAGING_TEST_PASSWORD_A",
  "STAGING_EVIDENCE_SERVICE_URL",
];
const missingStagingVariables = requiredStagingVariables.filter(
  (name) => !process.env[name]?.trim(),
);
if (missingStagingVariables.length > 0) {
  throw new Error(
    `Hosted Staging smoke blocked; missing required settings: ${missingStagingVariables.join(", ")}.`,
  );
}
const requiredAuthorizations = {
  STAGING_ALLOW_AUTHENTICATED_PREVIEW_REQUESTS:
    "I_CONFIRM_STAGING_PREVIEW_USES_STAGING_SUPABASE_AND_EVIDENCE_SERVICE",
  STAGING_ALLOW_EVIDENCE_SERVICE_REQUESTS: "I_CONFIRM_STAGING_EVIDENCE_SERVICE",
};
const missingAuthorizations = Object.entries(requiredAuthorizations)
  .filter(([name, expected]) => process.env[name] !== expected)
  .map(([name]) => name);
if (missingAuthorizations.length > 0) {
  throw new Error(
    `Hosted Staging smoke blocked; explicit target confirmations are required: ${missingAuthorizations.join(", ")}.`,
  );
}

export default defineConfig({
  testDir: "./tests/hosted",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: appUrl ?? "https://staging-preview-not-configured.invalid",
    storageState: storageState || undefined,
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
