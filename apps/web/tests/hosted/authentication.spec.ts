import { createPublicKey, verify as verifyJwtSignature } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const STAGING_PROJECT_REF = "slxfsjnsrvfkdtxayiud";
const STAGING_SUPABASE_ORIGIN = `https://${STAGING_PROJECT_REF}.supabase.co`;
const STAGING_ISSUER = `${STAGING_SUPABASE_ORIGIN}/auth/v1`;
const stagingAppUrl = process.env.STAGING_APP_URL;
const stagingAppOrigin = stagingAppUrl ? new URL(stagingAppUrl).origin : undefined;
const evidenceServiceUrl = getStagingEvidenceServiceUrl();
const authenticatedPreviewAuthorized =
  process.env.STAGING_ALLOW_AUTHENTICATED_PREVIEW_REQUESTS ===
  "I_CONFIRM_STAGING_PREVIEW_USES_STAGING_SUPABASE_AND_EVIDENCE_SERVICE";
const evidenceServiceRequestsAuthorized =
  process.env.STAGING_ALLOW_EVIDENCE_SERVICE_REQUESTS ===
  "I_CONFIRM_STAGING_EVIDENCE_SERVICE";
const provisioningAuthorized =
  process.env.STAGING_ALLOW_PERSONAL_WORKSPACE_PROVISIONING ===
  "I_AUTHORIZE_STAGING_PERSONAL_WORKSPACE_PROVISIONING";

type Account = { email: string; password: string };
type IdentityResult = {
  status: number;
  userId?: string;
  workspaceIds: string[];
  workspaceRolesValid: boolean;
};

function accountFromEnvironment(suffix: "A" | "B"): Account | undefined {
  const email = process.env[`STAGING_TEST_EMAIL_${suffix}`];
  const password = process.env[`STAGING_TEST_PASSWORD_${suffix}`];
  return email && password ? { email, password } : undefined;
}

function getStagingEvidenceServiceUrl(): string | undefined {
  const configured = process.env.STAGING_EVIDENCE_SERVICE_URL;
  if (!configured) return undefined;

  let parsed: URL;
  try {
    parsed = new URL(configured);
  } catch {
    throw new Error("STAGING_EVIDENCE_SERVICE_URL must be a valid HTTPS origin.");
  }
  if (
    parsed.protocol !== "https:" ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    !parsed.hostname.split(".").some((label) =>
      /(?:^|-)(?:staging|stage|test|preview)(?:-|$)/i.test(label),
    )
  ) {
    throw new Error("STAGING_EVIDENCE_SERVICE_URL must be an HTTPS origin with a staging, stage, test, or preview hostname label.");
  }
  return parsed.origin;
}

async function openPreviewLogin(
  page: Page,
  onStagingAuthRequest?: () => void,
): Promise<void> {
  test.skip(!stagingAppOrigin, "BLOCKED: STAGING_APP_URL is not configured.");
  await page.route((url) => (
    url.hostname.endsWith(".supabase.co") || url.pathname.startsWith("/auth/v1/")
  ), async (route) => {
    const url = new URL(route.request().url());
    const headers = await route.request().allHeaders();
    if (
      url.origin !== STAGING_SUPABASE_ORIGIN ||
      !headers.apikey?.startsWith("sb_publishable_")
    ) {
      await route.abort();
      return;
    }
    if (url.pathname === "/auth/v1/token") onStagingAuthRequest?.();
    await route.continue();
  });
  await page.goto("/login", { waitUntil: "domcontentloaded" });
  const current = new URL(page.url());
  test.skip(
    current.origin !== stagingAppOrigin || current.pathname !== "/login",
    "BLOCKED: authenticate through Vercel SSO and provide the resulting authorized storage state.",
  );
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
}

async function readIdentity(page: Page): Promise<IdentityResult> {
  return page.evaluate(async () => {
    const response = await fetch("/api/evidence/me", { cache: "no-store" });
    if (!response.ok) {
      return { status: response.status, workspaceIds: [], workspaceRolesValid: false };
    }
    const body: unknown = await response.json();
    if (!body || typeof body !== "object") {
      return { status: response.status, workspaceIds: [], workspaceRolesValid: false };
    }
    const candidate = body as {
      user_id?: unknown;
      workspaces?: unknown;
    };
    const workspaces = Array.isArray(candidate.workspaces) ? candidate.workspaces : [];
    const workspaceIds = workspaces.flatMap((workspace) => {
      if (!workspace || typeof workspace !== "object") return [];
      const candidateWorkspace = workspace as { id?: unknown };
      if (typeof candidateWorkspace.id === "string") return [candidateWorkspace.id];
      return [];
    });
    const workspaceRolesValid = workspaces.every(
      (workspace) => {
        if (!workspace || typeof workspace !== "object") return false;
        const candidateWorkspace = workspace as { role?: unknown };
        return candidateWorkspace.role === "owner" || candidateWorkspace.role === "member";
      },
    );
    return {
      status: response.status,
      userId: typeof candidate.user_id === "string" ? candidate.user_id : undefined,
      workspaceIds,
      workspaceRolesValid,
    };
  });
}

async function signInAndReadIdentity(
  page: Page,
  account: Account,
): Promise<{ identity: IdentityResult; stagingAuthTargetMatched: boolean; provisioningStatuses: number[] }> {
  let stagingAuthTargetMatched = false;
  const provisioningStatuses: number[] = [];

  if (!provisioningAuthorized) {
    await page.route("**/api/evidence/personal-workspace", (route) =>
      route.fulfill({
        status: 403,
        contentType: "application/json",
        body: JSON.stringify({ error: "Workspace provisioning was not authorized for this test run." }),
      }),
    );
  } else {
    page.on("response", (response) => {
      if (new URL(response.url()).pathname === "/api/evidence/personal-workspace") {
        provisioningStatuses.push(response.status());
      }
    });
  }

  await openPreviewLogin(page, () => { stagingAuthTargetMatched = true; });
  await page.getByLabel("Email").fill(account.email);
  await page.getByLabel("Password").fill(account.password);
  await page.getByRole("button", { name: "Sign in" }).click();

  if (provisioningAuthorized) {
    await expect(page).toHaveURL(`${stagingAppOrigin}/`);
  } else {
    await expect(page.getByRole("alert")).toContainText(
      "Your account is signed in, but your workspace could not be opened.",
    );
    await page.goto("/");
  }

  await expect(page.getByRole("button", { name: "Open account" })).toBeVisible();
  const identity = await readIdentity(page);
  return { identity, stagingAuthTargetMatched, provisioningStatuses };
}

function isUuid(value: string | undefined): boolean {
  return Boolean(
    value &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value),
  );
}

test("unauthenticated GET /api/evidence/me is rejected", async ({ page }) => {
  await openPreviewLogin(page);
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/evidence/me", { cache: "no-store" });
    const isJson = response.headers.get("content-type")?.includes("application/json") ?? false;
    let authenticationRequired = false;
    if (isJson) {
      const body: unknown = await response.json();
      if (body && typeof body === "object") {
        authenticationRequired =
          (body as { error?: unknown }).error === "Authentication is required.";
      }
    }
    return { status: response.status, isJson, authenticationRequired };
  });
  expect(result).toEqual({
    status: 401,
    isJson: true,
    authenticationRequired: true,
  });
});

test("authorized Staging account signs in and resolves its verified identity", async ({ page }) => {
  if (!authenticatedPreviewAuthorized) {
    test.skip(
      true,
      "BLOCKED: verify the Preview's effective Supabase and Evidence Service targets before authorizing authenticated requests.",
    );
    return;
  }
  const accountA = accountFromEnvironment("A");
  if (!accountA) {
    test.skip(true, "BLOCKED: STAGING_TEST_EMAIL_A and STAGING_TEST_PASSWORD_A are unavailable.");
    return;
  }

  const result = await signInAndReadIdentity(page, accountA);
  expect(result.stagingAuthTargetMatched).toBe(true);
  expect(result.identity.status).toBe(200);
  expect(isUuid(result.identity.userId)).toBe(true);
  expect(result.identity.workspaceRolesValid).toBe(true);
  if (provisioningAuthorized) {
    expect(result.provisioningStatuses).toContain(200);
  } else {
    expect(result.provisioningStatuses).toHaveLength(0);
  }
});

test("account A cannot read account B's workspace", async ({ page, browser }) => {
  if (!authenticatedPreviewAuthorized) {
    test.skip(
      true,
      "BLOCKED: verify the Preview's effective Supabase and Evidence Service targets before authorizing authenticated requests.",
    );
    return;
  }
  const accountA = accountFromEnvironment("A");
  const accountB = accountFromEnvironment("B");
  if (!accountA || !accountB) {
    test.skip(true, "BLOCKED: both authorized Staging test identities are required.");
    return;
  }

  const resultA = await signInAndReadIdentity(page, accountA);
  expect(resultA.stagingAuthTargetMatched).toBe(true);
  expect(resultA.identity.status).toBe(200);
  expect(isUuid(resultA.identity.userId)).toBe(true);

  const context = await browser.newContext({
    storageState: process.env.STAGING_VERCEL_STORAGE_STATE || undefined,
  });
  try {
    const pageB = await context.newPage();
    const resultB = await signInAndReadIdentity(pageB, accountB);
    expect(resultB.stagingAuthTargetMatched).toBe(true);
    expect(resultB.identity.status).toBe(200);
    expect(isUuid(resultB.identity.userId)).toBe(true);
    expect(resultA.identity.userId !== resultB.identity.userId).toBe(true);

    const workspaceB = resultB.identity.workspaceIds[0];
    if (!workspaceB) {
      test.skip(true, "BLOCKED: account B has no active workspace to test isolation against.");
      return;
    }
    const attempt = await page.evaluate(async (workspaceId) => {
      const response = await fetch(
        `/api/evidence/workspaces/${encodeURIComponent(workspaceId)}`,
        { cache: "no-store" },
      );
      return { status: response.status };
    }, workspaceB);
    expect(attempt.status).toBe(404);
  } finally {
    await context.close();
  }
});

test("Evidence Service rejects an invalid JWT", async ({ request }) => {
  if (!evidenceServiceRequestsAuthorized) {
    test.skip(true, "BLOCKED: confirm that STAGING_EVIDENCE_SERVICE_URL identifies the Staging service.");
    return;
  }
  test.skip(!evidenceServiceUrl, "BLOCKED: STAGING_EVIDENCE_SERVICE_URL is unavailable.");
  const response = await request.get(`${evidenceServiceUrl}/v1/me`, {
    headers: { Authorization: "Bearer invalid-staging-smoke-token" },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(401);
});

test("Evidence Service rejects a previously issued expired Staging JWT", async ({ request }) => {
  const expiredToken = process.env.STAGING_EXPIRED_ACCESS_TOKEN;
  if (!evidenceServiceRequestsAuthorized) {
    test.skip(true, "BLOCKED: confirm that STAGING_EVIDENCE_SERVICE_URL identifies the Staging service.");
    return;
  }
  test.skip(!evidenceServiceUrl, "BLOCKED: STAGING_EVIDENCE_SERVICE_URL is unavailable.");
  if (!expiredToken) {
    test.skip(true, "BLOCKED: no previously issued expired Staging access token was provided.");
    return;
  }

  let header: { alg?: unknown; kid?: unknown; typ?: unknown };
  let claims: {
    aud?: unknown;
    exp?: unknown;
    iat?: unknown;
    is_anonymous?: unknown;
    iss?: unknown;
    role?: unknown;
    sub?: unknown;
  };
  let signedContent: Buffer;
  let signature: Buffer;
  try {
    const segments = expiredToken.split(".");
    if (segments.length !== 3) throw new Error();
    header = JSON.parse(Buffer.from(segments[0], "base64url").toString("utf8"));
    claims = JSON.parse(Buffer.from(segments[1], "base64url").toString("utf8"));
    signedContent = Buffer.from(`${segments[0]}.${segments[1]}`);
    signature = Buffer.from(segments[2], "base64url");
  } catch {
    test.skip(true, "BLOCKED: the supplied token is not a JWT.");
    return;
  }
  test.skip(
    header.typ !== "JWT" ||
      (header.alg !== "ES256" && header.alg !== "RS256") ||
      typeof header.kid !== "string" ||
      claims.aud !== "authenticated" ||
      claims.role !== "authenticated" ||
      claims.is_anonymous !== false ||
      !isUuid(typeof claims.sub === "string" ? claims.sub : undefined) ||
      typeof claims.iat !== "number" ||
      claims.iat > Math.floor(Date.now() / 1000) + 30 ||
      typeof claims.exp !== "number" ||
      claims.exp >= Math.floor(Date.now() / 1000) - 30 ||
      claims.iss !== STAGING_ISSUER,
    "BLOCKED: the supplied JWT is not a previously issued, expired Staging user token.",
  );

  const jwksResponse = await request.get(`${STAGING_ISSUER}/.well-known/jwks.json`, {
    maxRedirects: 0,
  });
  test.skip(jwksResponse.status() !== 200, "BLOCKED: the Staging JWKS endpoint is unavailable.");
  const jwks = await jwksResponse.json() as {
    keys?: Array<{
      alg?: string;
      crv?: string;
      e?: string;
      kty?: string;
      kid?: string;
      n?: string;
      use?: string;
      x?: string;
      y?: string;
    }>;
  };
  const signingKey = jwks.keys?.find(
    (key) =>
      key.kid === header.kid &&
      key.alg === header.alg &&
      key.use === "sig" &&
      (key.alg === "ES256" || key.alg === "RS256"),
  );
  if (!signingKey) {
    test.skip(true, "BLOCKED: the expired JWT signing key is not present in the Staging JWKS.");
    return;
  }

  let signatureIsValid = false;
  try {
    let publicKey: ReturnType<typeof createPublicKey>;
    if (
      signingKey.kty === "EC" &&
      typeof signingKey.crv === "string" &&
      typeof signingKey.x === "string" &&
      typeof signingKey.y === "string"
    ) {
      publicKey = createPublicKey({
        key: {
          kty: "EC",
          crv: signingKey.crv,
          x: signingKey.x,
          y: signingKey.y,
        },
        format: "jwk",
      });
    } else if (
      signingKey.kty === "RSA" &&
      typeof signingKey.n === "string" &&
      typeof signingKey.e === "string"
    ) {
      publicKey = createPublicKey({
        key: { kty: "RSA", n: signingKey.n, e: signingKey.e },
        format: "jwk",
      });
    } else {
      test.skip(true, "BLOCKED: the Staging JWKS key type is unsupported for signature verification.");
      return;
    }
    signatureIsValid = verifyJwtSignature(
      "sha256",
      signedContent,
      signingKey.alg === "ES256"
        ? { key: publicKey, dsaEncoding: "ieee-p1363" }
        : publicKey,
      signature,
    );
  } catch {
    test.skip(true, "BLOCKED: the expired JWT could not be verified against the Staging JWKS.");
    return;
  }
  test.skip(!signatureIsValid, "BLOCKED: the expired JWT signature is not valid for the Staging JWKS.");

  const response = await request.get(`${evidenceServiceUrl}/v1/me`, {
    headers: { Authorization: `Bearer ${expiredToken}` },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(401);
});
