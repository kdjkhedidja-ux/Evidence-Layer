import { expect, test } from "@playwright/test";

const email = "person@example.test";
const secondEmail = "switch@example.test";
const password = "Synthetic-password-123";
const productWorkspaceId = "10000000-0000-4000-8000-000000000002";
const productEvidenceId = "10000000-0000-4000-8000-000000000003";

test("login authenticates with the local fixture and opens Workspace", async ({ page }) => {
  await page.route("**/api/evidence/personal-workspace", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }),
  );
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL("/workspace");
  await expect(page.getByRole("heading", { name: "Evidence workspace" })).toBeVisible();
});

test("browser session cookies reach the verified server-side workspace API", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL("http://localhost:3000/workspace");
  await expect(page.getByRole("heading", { name: "Evidence workspace" })).toBeVisible();

  const identity = await page.evaluate(async () => {
    const response = await fetch("/api/evidence/me", { cache: "no-store" });
    return { status: response.status, body: await response.json() };
  });
  expect(identity).toEqual({
    status: 200,
    body: {
      user_id: "synthetic-ui-user",
      account_type: "individual",
      organization_name: null,
      can_manage_workspace: false,
      workspaces: [{
        id: productWorkspaceId,
        name: "First synthetic workspace",
        role: "owner",
      }],
    },
  });

  const workspace = await page.evaluate(async (workspaceId) => {
    const response = await fetch(`/api/evidence/workspaces/${workspaceId}`, { cache: "no-store" });
    return { status: response.status, body: await response.json() };
  }, productWorkspaceId);
  expect(workspace).toEqual({
    status: 200,
    body: { id: productWorkspaceId, name: "First synthetic workspace", role: "owner" },
  });
});

test("server workspace APIs reject absent sessions and workspaces outside a user's membership", async ({ page }) => {
  await page.goto("/login");
  const unauthenticated = await page.evaluate(async () => {
    const response = await fetch("/api/evidence/me", { cache: "no-store" });
    return { status: response.status, body: await response.json() };
  });
  expect(unauthenticated).toEqual({
    status: 401,
    body: { error: "Authentication is required." },
  });

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("http://localhost:3000/workspace");

  const unauthorized = await page.evaluate(async () => {
    const response = await fetch("/api/evidence/workspaces/10000000-0000-4000-8000-000000000004", {
      cache: "no-store",
    });
    return { status: response.status, body: await response.json() };
  });
  expect(unauthorized).toEqual({ status: 404, body: { error: "Workspace not found." } });
});

test("a workspace failure keeps the signed-in session and can be retried", async ({ page }) => {
  let attempts = 0;
  await page.route("**/api/evidence/personal-workspace", (route) => {
    attempts += 1;
    return route.fulfill({
      status: attempts === 1 ? 503 : 200,
      contentType: "application/json",
      body: JSON.stringify(attempts === 1 ? { error: "synthetic workspace failure" } : { ok: true }),
    });
  });
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.locator(".auth-error[role='alert']")).toContainText("workspace could not be opened");
  await expect(page).toHaveURL("http://localhost:3000/login");
  await page.getByRole("button", { name: "Retry opening workspace" }).click();
  await expect(page).toHaveURL("http://localhost:3000/workspace");
  await expect(page.getByRole("heading", { name: "Evidence workspace" })).toBeVisible();
  expect(attempts).toBe(2);
});

test("account panel signs out cleanly before a different synthetic account signs in", async ({ page }) => {
  const accounts = [
    {
      email,
      id: "synthetic-ui-user",
      workspace: { id: "synthetic-workspace-one", name: "First synthetic workspace", role: "owner" },
    },
    {
      email: secondEmail,
      id: "synthetic-ui-user-two",
      workspace: { id: "synthetic-workspace-two", name: "Second synthetic workspace", role: "owner" },
    },
  ];
  let activeAccount = accounts[0];
  await page.route("**/api/evidence/personal-workspace", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(activeAccount.workspace) }),
  );
  await page.route("**/api/evidence/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ user_id: activeAccount.id, workspaces: [activeAccount.workspace] }),
  }));

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill(activeAccount.email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("http://localhost:3000/");
  await page.getByRole("button", { name: "Account" }).click();

  const accountPanel = page.getByRole("region", { name: "ACCOUNT WORKSPACE" });
  await expect(accountPanel).toBeVisible();
  await expect(accountPanel.getByText(email, { exact: true })).toBeVisible();
  await expect(accountPanel.getByText(accounts[0].workspace.name, { exact: true })).toBeVisible();
  await accountPanel.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL("http://localhost:3000/login?reason=signed-out");
  await expect(page.getByRole("status")).toContainText("You have been signed out.");

  activeAccount = accounts[1];
  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill(activeAccount.email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("http://localhost:3000/");
  await page.getByRole("button", { name: "Account" }).click();

  const switchedPanel = page.getByRole("region", { name: "ACCOUNT WORKSPACE" });
  await expect(switchedPanel.getByText(secondEmail, { exact: true })).toBeVisible();
  await expect(switchedPanel.getByText(accounts[1].workspace.name, { exact: true })).toBeVisible();
  await expect(switchedPanel.getByText(accounts[0].workspace.name, { exact: true })).toHaveCount(0);
});

test("workspace route shows authorized service records on desktop and mobile", async ({ page }) => {
  await page.route("**/api/evidence/personal-workspace", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      id: productWorkspaceId,
      name: "Isolated test workspace",
      role: "owner",
    }),
  }));
  await page.route("**/api/evidence/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: "synthetic-ui-user",
      workspaces: [{ id: productWorkspaceId, name: "Isolated test workspace", role: "owner" }],
    }),
  }));
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/evidence`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{
        id: productEvidenceId,
        workspace_id: productWorkspaceId,
        content: "Isolated live-service fixture record",
        source_label: "Browser test fixture",
        source_id: null,
        source_version_id: null,
        created_at: "2026-10-31T12:00:00Z",
        updated_at: "2026-10-31T12:00:00Z",
      }]),
    }),
  );
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/sources`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("/workspace");

  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(page.getByRole("heading", { name: "Evidence workspace" })).toBeVisible();
  await expect(page.getByText("Isolated live-service fixture record")).toBeVisible();
  await expect(page.getByText("Live service data")).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Capability Catalog" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("workspace sources index lists versions and reads one through the verified route", async ({ page }) => {
  const sourceId = "10000000-0000-4000-8000-000000000004";
  const versionId = "10000000-0000-4000-8000-000000000005";
  await page.route("**/api/evidence/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: "synthetic-ui-user",
      workspaces: [{ id: productWorkspaceId, name: "Isolated test workspace", role: "owner" }],
    }),
  }));
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/evidence`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/sources`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{
        source_id: sourceId,
        version_id: versionId,
        state: "published",
        sha256: "a".repeat(64),
        size_bytes: 25,
        created_at: "2026-10-31T12:00:00Z",
      }]),
    }),
  );
  await page.route(
    `**/api/evidence/workspaces/${productWorkspaceId}/sources/${sourceId}/versions/${versionId}/content`,
    (route) => route.fulfill({
      status: 200,
      contentType: "application/octet-stream",
      headers: { "Content-Digest": "sha-256=:YWJjZA==:" },
      body: "verified fixture source bytes",
    }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("/workspace");

  await expect(page.getByRole("heading", { name: "Workspace sources" })).toBeVisible();
  await expect(page.getByText(/Version 10000000/)).toBeVisible();
  await expect(page.getByText(/· Published ·/)).toBeVisible();

  await page.getByRole("button", { name: "Read source" }).click();
  await expect(page.getByText("verified fixture source bytes")).toBeVisible();
  await expect(page.getByText("sha-256=:YWJjZA==:")).toBeVisible();
  await expect(page.getByText("Source access was verified for this read.")).toBeVisible();
});

test("workspace sources read denial shows a safe message and no content", async ({ page }) => {
  const sourceId = "10000000-0000-4000-8000-000000000004";
  const versionId = "10000000-0000-4000-8000-000000000005";
  await page.route("**/api/evidence/me", (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      user_id: "synthetic-ui-user",
      workspaces: [{ id: productWorkspaceId, name: "Isolated test workspace", role: "owner" }],
    }),
  }));
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/evidence`, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  await page.route(`**/api/evidence/workspaces/${productWorkspaceId}/sources`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([{
        source_id: sourceId,
        version_id: versionId,
        state: "published",
        sha256: "a".repeat(64),
        size_bytes: 25,
        created_at: "2026-10-31T12:00:00Z",
      }]),
    }),
  );
  await page.route(
    `**/api/evidence/workspaces/${productWorkspaceId}/sources/${sourceId}/versions/${versionId}/content`,
    (route) => route.fulfill({
      status: 403,
      contentType: "application/json",
      body: JSON.stringify({ error: "Source read is not authorized." }),
    }),
  );

  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL("/workspace");

  await page.getByRole("button", { name: "Read source" }).click();
  await expect(page.getByText("Source read is not authorized.")).toBeVisible();
  await expect(page.getByText("verified fixture source bytes")).not.toBeVisible();
});

test("login normalizes an unsafe external next destination to the app root", async ({ page }) => {
  await page.route("**/api/evidence/personal-workspace", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }),
  );
  await page.goto("/login?next=https%3A%2F%2Fevil.example%2Foutside");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL("http://localhost:3000/");
});

test("login failure is announced accessibly", async ({ page }) => {
  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("incorrect synthetic password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page.locator(".auth-error[role='alert']")).toHaveText(
    "We could not sign you in. Check your details or try again later.",
  );
});

test("login prevents duplicate submissions while the local Auth request is pending", async ({ page }) => {
  let releaseAuthRequest!: () => void;
  let markAuthRequestStarted!: () => void;
  const heldRequest = new Promise<void>((resolve) => { releaseAuthRequest = resolve; });
  const authRequestStarted = new Promise<void>((resolve) => { markAuthRequestStarted = resolve; });
  let passwordRequests = 0;

  await page.route((url) => url.pathname === "/auth/v1/token" && url.searchParams.get("grant_type") === "password", async (route) => {
    passwordRequests += 1;
    if (passwordRequests === 1) {
      markAuthRequestStarted();
      await heldRequest;
      await route.continue();
    } else {
      await route.abort();
    }
  });
  await page.route("**/api/evidence/personal-workspace", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }),
  );

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  const submit = page.locator(".auth-submit");
  const form = page.locator(".auth-form");

  try {
    await submit.click();
    await authRequestStarted;
    await expect(submit).toBeDisabled();
    await expect(form).toHaveAttribute("aria-busy", "true");
    await form.evaluate((element) => (element as HTMLFormElement).requestSubmit());
    releaseAuthRequest();

    await expect(page).toHaveURL("http://localhost:3000/");
    await expect(page.getByRole("heading", { name: "Evidence Explorer" })).toBeVisible();
    expect(passwordRequests).toBe(1);
  } finally {
    releaseAuthRequest();
  }
});

test("signup displays its confirmation status without sending real email", async ({ page }) => {
  await page.goto("/signup");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page.getByRole("status")).toHaveText(
    "Check your email for a confirmation link. You can sign in after confirming your address.",
  );
});

test("signup reports required and malformed fields through native browser validation", async ({ page }) => {
  let passwordRequests = 0;
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/auth/v1/token" || url.pathname === "/auth/v1/signup") passwordRequests += 1;
  });

  await page.goto("/signup");
  const emailInput = page.getByLabel("Email");
  const passwordInput = page.getByLabel("Password", { exact: true });
  const confirmationInput = page.getByLabel("Confirm password");
  await expect(emailInput).toHaveAttribute("required", "");
  await expect(passwordInput).toHaveAttribute("required", "");
  await expect(confirmationInput).toHaveAttribute("required", "");

  await page.getByRole("button", { name: "Create account" }).click();
  await expect(emailInput).toBeFocused();
  expect(await emailInput.evaluate((input) => (input as HTMLInputElement).validity.valid)).toBe(false);
  expect(await emailInput.evaluate((input) => (input as HTMLInputElement).validity.valueMissing)).toBe(true);

  await emailInput.fill("not-an-email");
  await page.getByRole("button", { name: "Create account" }).click();
  expect(await emailInput.evaluate((input) => (input as HTMLInputElement).validity.valid)).toBe(false);
  expect(await emailInput.evaluate((input) => (input as HTMLInputElement).validity.typeMismatch)).toBe(true);
  expect(passwordRequests).toBe(0);
});

test("forgot-password displays a non-enumerating reset status", async ({ page }) => {
  await page.goto("/forgot-password");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "Send reset link" }).click();

  await expect(page.getByRole("status")).toHaveText(
    "If an account matches that address, a reset link will arrive by email.",
  );
});

test("recovery callback opens reset-password and completes using the synthetic Auth fixture", async ({ page }) => {
  await page.goto("/auth/callback?token_hash=synthetic-recovery-link&type=recovery");
  await expect(page).toHaveURL(/\/reset-password$/);
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password").fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Update password" }).click();

  await expect(page.getByRole("status")).toHaveText("Password updated. Sign in with your new password.");
});

test("expired recovery token is rejected by the deterministic local fixture", async ({ page }) => {
  await page.goto("/auth/callback?token_hash=synthetic-expired-recovery-link&type=recovery");

  await expect(page).toHaveURL("http://localhost:3000/auth/error?reason=invalid-link");
  await expect(page.getByRole("heading", { name: "This link could not be verified" })).toBeVisible();
  await expect(page.locator("body")).not.toContainText("synthetic-expired-recovery-link");
});

test("callback errors show safe, accessible guidance", async ({ page }) => {
  await page.goto(
    "/auth/callback?error=access_denied&error_code=otp_expired&error_description=synthetic-private-detail",
  );

  await expect(page).toHaveURL(/\/auth\/error\?reason=expired-link$/);
  await expect(page.getByRole("heading", { name: "This link has expired" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Request a password-reset link" })).toHaveAttribute(
    "href",
    "/forgot-password",
  );
  await expect(page.locator("body")).not.toContainText("synthetic-private-detail");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`signed-out sidebar navigation reaches login on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/");

    if (viewport.width <= 900) {
      const menuButton = page.getByRole("button", { name: "Menu" });
      await menuButton.click();
      await expect(page.getByRole("button", { name: "Close" })).toHaveAttribute(
        "aria-expanded",
        "true",
      );
    }

    await page.getByRole("complementary").getByRole("link", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 390, height: 844 },
]) {
  test(`login layout, keyboard focus, and themes work on ${viewport.name}`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(await page.evaluate(() => document.documentElement.scrollWidth))
      .toBeLessThanOrEqual(viewport.width);

    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Evidence Layer sign in" })).toBeFocused();
    await page.keyboard.press("Tab");
    const emailInput = page.getByLabel("Email");
    await expect(emailInput).toBeFocused();
    await expect(emailInput).toHaveCSS("outline-style", "solid");

    await page.evaluate(() => localStorage.setItem("evidence-layer-theme", "dark"));
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
  });
}
