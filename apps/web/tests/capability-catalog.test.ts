import { describe, expect, it } from "vitest";
import { CAPABILITY_GROUPS } from "../app/workspace/capability-catalog";

const REQUIRED_FEATURES: Record<string, string[]> = {
  Authentication: ["Identity", "Sessions", "Sign out"],
  "Workspace & Access": ["Membership", "Members", "Roles", "Permissions"],
  "Evidence Records": ["Browse", "Create", "Edit", "Delete", "Provenance", "Source links"],
  "Evidence Relationships": [
    "Relationship types",
    "Evidence links",
    "Rationale",
    "Review basis",
  ],
  "Task Context": ["Task-focused retrieval", "Relationship-aware selection"],
  "Reading Guide": ["Reading instructions", "Source inspection", "Provenance guidance"],
  Verification: ["Snapshot integrity", "Hash verification"],
  Evaluation: ["Historical reports", "Comparisons"],
  "Cost & Performance": ["Cost estimates", "Token usage", "API calls", "Latency"],
  "Settings & Access": [
    "Account profile",
    "Security",
    "Authentication sessions",
    "Personal API Keys",
    "Remote MCP",
  ],
};

const ALLOWED_STATUSES = new Set([
  "Connected",
  "Partially available",
  "Evidence-grounded",
  "Historical",
  "Pending",
  "Unavailable",
  "Limited / Read only",
]);

describe("workspace capability catalog contract", () => {
  it("contains every approved service and subfeature once", () => {
    expect(CAPABILITY_GROUPS.map((group) => group.name)).toEqual(Object.keys(REQUIRED_FEATURES));
    for (const group of CAPABILITY_GROUPS) {
      expect(group.features.map((feature) => feature.name)).toEqual(REQUIRED_FEATURES[group.name]);
      expect(new Set(group.features.map((feature) => feature.name)).size).toBe(group.features.length);
    }
  });

  it("uses only the approved status vocabulary and real existing destinations", () => {
    const existingDestinations = new Set([
      "/?account=1#account-panel",
      "/?account=1#account-profile",
      "/?account=1#account-session-title",
      "/workspace#personal-api-keys",
      "/workspace#remote-mcp",
      "/workspace#membership-heading",
      "/mfa",
      "/?account=1#account-organization-title",
      "/#explorer",
      "/workspace#records-heading",
      "/workspace#source-read-heading",
      "/relationships",
      "/evaluation",
      "/cost-performance",
      "/workspace#agent-connection",
      "/workspace#task-context",
      "/workspace#task-measurement",
    ]);
    const features = CAPABILITY_GROUPS.flatMap((group) => group.features);
    for (const feature of features) {
      expect(ALLOWED_STATUSES.has(feature.status)).toBe(true);
      if (feature.href) expect(existingDestinations.has(feature.href)).toBe(true);
    }
  });

  it("keeps evaluation and cost accounting honest and API keys planned", () => {
    const features = new Map(
      CAPABILITY_GROUPS.flatMap((group) => group.features.map((feature) => [feature.name, feature] as const)),
    );
    expect(features.get("Historical reports")?.status).toBe("Historical");
    expect(features.get("Token usage")?.detail).toMatch(/not per-workspace/i);
    expect(features.get("Personal API Keys")?.status).toBe("Partially available");
    expect(features.get("Personal API Keys")?.detail).toMatch(/read-only keys/i);
  });
});
