import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createClient } from "@supabase/supabase-js";

// Run only against the disposable local Supabase stack. Never read hosted URLs.
const status = JSON.parse(execFileSync(process.env.SUPABASE_CLI || "supabase", ["status", "-o", "json"], {
  cwd: new URL("../../..", import.meta.url),
  encoding: "utf8",
  stdio: ["ignore", "pipe", "ignore"],
}));
const apiUrl = status.API_URL;
const publishableKey = status.PUBLISHABLE_KEY;
const mailUrl = status.MAILPIT_URL || status.INBUCKET_URL || "http://127.0.0.1:54324";
assert.equal(new URL(apiUrl).hostname, "127.0.0.1", "Local Supabase is required");
assert.equal(new URL(mailUrl).hostname, "127.0.0.1", "Local mail capture is required");
assert.ok(publishableKey, "Local API key is required");

function client() {
  const storage = new Map();
  return createClient(apiUrl, publishableKey, {
    auth: {
      flowType: "pkce",
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: true,
      storage: {
        getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: (key) => storage.delete(key),
      },
    },
  });
}

async function messages() {
  const response = await fetch(`${mailUrl}/api/v1/messages`);
  assert.equal(response.status, 200, "Local mail capture is unavailable");
  return (await response.json()).messages;
}

async function nextEmail(email, seen) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const match = (await messages()).find(
      (message) => !seen.has(message.ID) && JSON.stringify(message.To).includes(email),
    );
    if (match) return match.ID;
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Expected local Auth email did not arrive");
}

async function confirmLink(messageId) {
  const response = await fetch(`${mailUrl}/api/v1/message/${encodeURIComponent(messageId)}`);
  assert.equal(response.status, 200, "Local Auth email could not be read");
  const message = await response.json();
  const html = message.HTML || message.Text || "";
  const normalized = html.replaceAll("&amp;", "&");
  const link = normalized.match(/https?:\/\/[^\s"'<>]+\/auth\/v1\/verify\?[^\s"'<>]+/)?.[0];
  assert.ok(link, "Auth email has no confirmation link");
  const url = new URL(link);
  assert.equal(url.origin, apiUrl, "Confirmation link must use local Auth");
  return url;
}

async function exchangeEmailLink(supabase, messageId, expectedNext) {
  const link = await confirmLink(messageId);
  const verification = await fetch(link, { redirect: "manual" });
  assert.ok([302, 303].includes(verification.status), "Auth link was not accepted");
  const destination = new URL(verification.headers.get("location"));
  assert.equal(destination.origin, "http://127.0.0.1:3000");
  assert.equal(destination.pathname, "/auth/callback");
  assert.equal(destination.searchParams.get("next"), expectedNext);
  const code = destination.searchParams.get("code");
  assert.ok(code, "PKCE callback has no authorization code");
  const { data, error } = await supabase.auth.exchangeCodeForSession(code);
  assert.equal(error, null, "PKCE code exchange failed");
  assert.ok(data.session?.access_token, "PKCE exchange returned no session");
  return data.session;
}

async function register(supabase, email, password) {
  const seen = new Set((await messages()).map((message) => message.ID));
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: { emailRedirectTo: "http://127.0.0.1:3000/auth/callback?next=%2F" },
  });
  assert.equal(error, null, "Signup failed");
  assert.ok(data.user, "Signup returned no user");
  assert.equal(data.session, null, "Unconfirmed signup must not receive a session");
  const beforeConfirmation = await client().auth.signInWithPassword({ email, password });
  assert.ok(beforeConfirmation.error, "Unconfirmed user was able to sign in");
  const messageId = await nextEmail(email, seen);
  const session = await exchangeEmailLink(supabase, messageId, "/");
  assert.equal(session.user.id, data.user.id);
  return data.user.id;
}

async function personalWorkspace(supabase) {
  const { data, error } = await supabase.rpc("ensure_personal_workspace");
  assert.equal(error, null, "Personal workspace RPC failed");
  assert.equal(data.length, 1);
  assert.equal(data[0].role, "owner");
  return data[0].workspace_id;
}

async function main() {
  const suffix = randomUUID();
  const password = `Start-${randomUUID()}!`;
  const updatedPassword = `New-${randomUUID()}!`;
  const firstEmail = `auth-a-${suffix}@example.test`;
  const secondEmail = `auth-b-${suffix}@example.test`;
  const first = client();
  const second = client();

  console.log("Checking signup and email confirmation...");
  const firstUserId = await register(first, firstEmail, password);
  await register(second, secondEmail, password);

  console.log("Checking personal workspaces and database isolation...");
  const firstWorkspace = await personalWorkspace(first);
  const secondWorkspace = await personalWorkspace(second);
  assert.notEqual(firstWorkspace, secondWorkspace);
  assert.equal(await personalWorkspace(first), firstWorkspace, "Provisioning must be idempotent");
  const inserted = await first.from("evidence_records")
    .insert({ workspace_id: firstWorkspace, content: "Local integration evidence" })
    .select("id").single();
  assert.equal(inserted.error, null, "Owner could not create evidence");
  const crossRead = await second.from("evidence_records").select("id")
    .eq("workspace_id", firstWorkspace);
  assert.equal(crossRead.error, null);
  assert.equal(crossRead.data.length, 0, "Cross-workspace evidence was visible");
  const crossWrite = await second.from("evidence_records")
    .insert({ workspace_id: firstWorkspace, content: "Forbidden" });
  assert.ok(crossWrite.error, "Cross-workspace evidence write succeeded");
  const anonymous = client();
  const anonWorkspace = await anonymous.rpc("ensure_personal_workspace");
  assert.ok(anonWorkspace.error, "Anonymous workspace provisioning succeeded");

  console.log("Checking password recovery and sign-in...");
  await first.auth.signOut();
  const seen = new Set((await messages()).map((message) => message.ID));
  const recovery = await first.auth.resetPasswordForEmail(firstEmail, {
    redirectTo: "http://127.0.0.1:3000/auth/callback?next=%2Freset-password",
  });
  assert.equal(recovery.error, null, "Recovery request failed");
  await exchangeEmailLink(first, await nextEmail(firstEmail, seen), "/reset-password");
  const changed = await first.auth.updateUser({ password: updatedPassword });
  assert.equal(changed.error, null, "Password update failed");
  await first.auth.signOut();
  const oldLogin = await first.auth.signInWithPassword({ email: firstEmail, password });
  assert.ok(oldLogin.error, "Old password still signs in");
  const newLogin = await first.auth.signInWithPassword({ email: firstEmail, password: updatedPassword });
  assert.equal(newLogin.error, null, "New password sign-in failed");
  assert.equal(newLogin.data.user.id, firstUserId);
  assert.equal(await personalWorkspace(first), firstWorkspace, "Sign-in created a second workspace");
  console.log("Local Auth and database integration passed.");
}

try {
  await main();
} catch {
  console.error("Local Auth integration failed in the last reported stage.");
  process.exitCode = 1;
}
