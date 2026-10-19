import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { getEvidenceServiceBaseUrl, getSupabasePublicConfig } from "../lib/supabase/config";

const initialEnvironment = {
  supabaseUrl: process.env.NEXT_PUBLIC_SUPABASE_URL,
  publishableKey: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  evidenceServiceUrl: process.env.EVIDENCE_SERVICE_URL,
};

afterEach(() => {
  if (initialEnvironment.supabaseUrl === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_URL;
  else process.env.NEXT_PUBLIC_SUPABASE_URL = initialEnvironment.supabaseUrl;
  if (initialEnvironment.publishableKey === undefined) delete process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  else process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = initialEnvironment.publishableKey;
  if (initialEnvironment.evidenceServiceUrl === undefined) delete process.env.EVIDENCE_SERVICE_URL;
  else process.env.EVIDENCE_SERVICE_URL = initialEnvironment.evidenceServiceUrl;
});

describe("public configuration and secret boundaries", () => {
  it("accepts only HTTPS project URLs and publishable-format public keys", () => {
    process.env.NEXT_PUBLIC_SUPABASE_URL = "https://sample-project.supabase.co";
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "sb_publishable_synthetic_placeholder";
    expect(getSupabasePublicConfig()).toEqual({
      url: "https://sample-project.supabase.co",
      publishableKey: "sb_publishable_synthetic_placeholder",
    });

    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY = "eyJhbGciOiJIUzI1NiJ9.synthetic";
    expect(getSupabasePublicConfig()).toBeNull();
    delete process.env.NEXT_PUBLIC_SUPABASE_URL;
    delete process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  });

  it("rejects credentials in the server-only service URL", () => {
    process.env.EVIDENCE_SERVICE_URL = "https://user:password@evidence.example";
    expect(getEvidenceServiceBaseUrl()).toBeNull();
    process.env.EVIDENCE_SERVICE_URL = "http://localhost:8000";
    expect(getEvidenceServiceBaseUrl()).toBe("http://localhost:8000");
    delete process.env.EVIDENCE_SERVICE_URL;
  });

  it("keeps the committed environment example to placeholders, not SMTP or server keys", () => {
    const example = readFileSync(resolve(process.cwd(), ".env.example"), "utf8");
    expect(example).toContain("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=sb_publishable_replace_with_project_key");
    expect(example).toContain("EVIDENCE_SERVICE_URL=http://localhost:8000");
    expect(example).not.toMatch(/RESEND_API_KEY|smtp_pass|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_SECRET_KEY/i);
  });
});
