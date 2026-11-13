export type CapabilityStatus =
  | "Connected"
  | "Partially available"
  | "Evidence-grounded"
  | "Historical"
  | "Pending"
  | "Unavailable"
  | "Limited / Read only";

export type Capability = {
  name: string;
  status: CapabilityStatus;
  detail: string;
  href?: string;
};

export type CapabilityGroup = {
  name: string;
  features: readonly Capability[];
};

export const CAPABILITY_GROUPS: readonly CapabilityGroup[] = [
  {
    name: "Authentication",
    features: [
      {
        name: "Identity",
        status: "Connected",
        detail: "Supabase Auth verifies the current user server-side before workspace data is returned.",
        href: "/?account=1#account-panel",
      },
      {
        name: "Sessions",
        status: "Partially available",
        detail: "The current browser session is verified and can be ended for every device. There is no session inventory or per-device management.",
        href: "/?account=1#account-session-title",
      },
      {
        name: "Sign out",
        status: "Connected",
        detail: "The current browser session can be ended through Supabase Auth.",
        href: "/?account=1#account-panel",
      },
    ],
  },
  {
    name: "Workspace & Access",
    features: [
      {
        name: "Membership",
        status: "Connected",
        detail: "The service lists the signed-in user's active workspaces and checks membership on workspace requests.",
        href: "/workspace#membership-heading",
      },
      {
        name: "Members",
        status: "Partially available",
        detail: "Organization admins can view the roster, add members by email, change roles, and remove members. Individual accounts only see their own memberships.",
        href: "/?account=1#account-organization-title",
      },
      {
        name: "Roles",
        status: "Partially available",
        detail: "The current owner, admin, or member role is shown. Organization admins can change member roles; group-workspace roles follow organization membership.",
        href: "/?account=1#account-organization-title",
      },
      {
        name: "Permissions",
        status: "Limited / Read only",
        detail: "Owner, admin, and member limits are enforced by the service and RLS. There is no per-workspace permissions editor.",
        href: "/?account=1#account-organization-title",
      },
    ],
  },
  {
    name: "Evidence Records",
    features: [
      {
        name: "Browse",
        status: "Connected",
        detail: "Authenticated members can read records in workspaces where they are active members.",
        href: "/#explorer",
      },
      {
        name: "Create",
        status: "Connected",
        detail: "Workspace owners and admins can create records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Edit",
        status: "Connected",
        detail: "Workspace owners and admins can edit records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Delete",
        status: "Connected",
        detail: "Workspace owners and admins can delete records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Provenance",
        status: "Evidence-grounded",
        detail: "Stored source labels and exact source-version identifiers remain attached to records when supplied.",
        href: "/#explorer",
      },
      {
        name: "Source links",
        status: "Evidence-grounded",
        detail: "Records may link to an exact published source version in the same workspace.",
        href: "/workspace#source-read-heading",
      },
    ],
  },
  {
    name: "Evidence Relationships",
    features: [
      {
        name: "Relationship types",
        status: "Connected",
        detail: "Explicit persisted links in four types: supports, contradicts, contextualizes, or derived from. Owners and admins write; members read.",
        href: "/relationships",
      },
      {
        name: "Evidence links",
        status: "Connected",
        detail: "Links connect two records in one workspace. Owners and admins write; members read.",
        href: "/relationships",
      },
      {
        name: "Rationale",
        status: "Connected",
        detail: "Every link stores the rationale supplied and maintained by an owner or admin.",
        href: "/relationships",
      },
      {
        name: "Review basis",
        status: "Connected",
        detail: "Every link stores its provenance or review basis; it is never generated automatically.",
        href: "/relationships",
      },
    ],
  },
  {
    name: "Task Context",
    features: [
      {
        name: "Task-focused retrieval",
        status: "Partially available",
        detail: "Combines the task, expected outcome, and context requirements to prepare bounded lexical evidence; it does not execute the agent task.",
        href: "/workspace#task-context",
      },
      {
        name: "Relationship-aware selection",
        status: "Partially available",
        detail: "Prioritizes up to three explicitly linked records for retrieved seed evidence and returns links only when both records are selected. JEV and semantic ranking are not connected.",
        href: "/workspace#task-context",
      },
    ],
  },
  {
    name: "Reading Guide",
    features: [
      {
        name: "Reading instructions",
        status: "Evidence-grounded",
        detail: "The guide explains how to inspect stored evidence without generating conclusions.",
        href: "/workspace#records-heading",
      },
      {
        name: "Source inspection",
        status: "Partially available",
        detail: "Authorized published UTF-8 source versions can be read through the verified service path.",
        href: "/workspace#source-read-heading",
      },
      {
        name: "Provenance guidance",
        status: "Evidence-grounded",
        detail: "The guide distinguishes stored evidence from its exact source and version; missing links remain explicit.",
        href: "/workspace#records-heading",
      },
    ],
  },
  {
    name: "Verification",
    features: [
      {
        name: "Snapshot integrity",
        status: "Connected",
        detail: "Only published versions that passed the verifier are marked integrity-verified; access is checked again during reads.",
        href: "/workspace#source-read-heading",
      },
      {
        name: "Hash verification",
        status: "Connected",
        detail: "The service verifies source bytes against the pinned SHA-256 before delivery and returns a Content-Digest header.",
        href: "/workspace#source-read-heading",
      },
    ],
  },
  {
    name: "Evaluation",
    features: [
      {
        name: "Historical reports",
        status: "Historical",
        detail: "Published benchmark reports are fixed repository artifacts, not live workspace evaluations.",
        href: "/evaluation",
      },
      {
        name: "Comparisons",
        status: "Historical",
        detail: "Available comparisons are historical benchmark results over fixed datasets.",
        href: "/evaluation",
      },
    ],
  },
  {
    name: "Cost & Performance",
    features: [
      {
        name: "Cost estimates",
        status: "Partially available",
        detail: "The planner estimates model and selection cost from request volume and published list prices; it is not an invoice.",
        href: "/cost-performance",
      },
      {
        name: "Token usage",
        status: "Partially available",
        detail: "The planner projects input and output tokens from measured reduction rates; it is not per-workspace usage data.",
        href: "/cost-performance",
      },
      {
        name: "API calls",
        status: "Partially available",
        detail: "The planner counts model, retrieval, and optional JEV calls from the configured route.",
        href: "/cost-performance",
      },
      {
        name: "Latency",
        status: "Partially available",
        detail: "The planner combines retrieval, TTFT, generation, and network assumptions into p50 and p95 estimates.",
        href: "/cost-performance",
      },
    ],
  },
  {
    name: "Settings & Access",
    features: [
      {
        name: "Account profile",
        status: "Connected",
        detail: "Change the account email with confirmation, or change the password with the current password and recent-session verification.",
        href: "/?account=1#account-profile",
      },
      {
        name: "Security",
        status: "Partially available",
        detail: "Passwords require at least 12 characters, and password changes require the current password plus a recent sign-in. TOTP authenticators can be enrolled and are challenged before app access.",
        href: "/mfa",
      },
      {
        name: "Authentication sessions",
        status: "Partially available",
        detail: "The current session can be signed out, and global sign-out revokes every session. Session inventory and per-device control are unavailable.",
        href: "/?account=1#account-session-title",
      },
      {
        name: "Personal API Keys",
        status: "Partially available",
        detail: "Workspace-scoped read-only keys for evidence and relationships, with one-time display, 90-day maximum expiry, revocation, and 60 reads per minute. Source bytes and writes require a user session.",
        href: "/workspace#personal-api-keys",
      },
      {
        name: "Remote MCP",
        status: "Partially available",
        detail: "Hosted Streamable HTTP MCP endpoint for retrieve_evidence and get_document using a personal API key; enabled by its own flag, not a connector flag. A local Python server is optional.",
        href: "/workspace#remote-mcp",
      },
    ],
  },
];
