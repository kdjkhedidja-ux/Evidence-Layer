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
        href: "/workspace#workspace-session",
      },
      {
        name: "Sessions",
        status: "Partially available",
        detail: "The current browser session is verified and can be ended for every device. There is no session inventory or per-device management.",
        href: "/workspace#session-management",
      },
      {
        name: "Sign out",
        status: "Connected",
        detail: "The current browser session can be ended through Supabase Auth.",
        href: "/workspace#workspace-session",
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
        status: "Limited / Read only",
        detail: "Only the signed-in user's memberships are listed; there is no workspace member-roster endpoint.",
        href: "/workspace#membership-heading",
      },
      {
        name: "Roles",
        status: "Limited / Read only",
        detail: "The current user's owner, admin, or member role is shown; role changes are unavailable.",
        href: "/workspace#membership-heading",
      },
      {
        name: "Permissions",
        status: "Limited / Read only",
        detail: "Current owner, admin, and member limits are enforced by the service and RLS. There is no permissions editor.",
        href: "/workspace#membership-heading",
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
        status: "Partially available",
        detail: "Workspace owners and admins can create records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Edit",
        status: "Partially available",
        detail: "Workspace owners and admins can edit records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Delete",
        status: "Partially available",
        detail: "Workspace owners and admins can delete records; members have read-only access.",
        href: "/workspace#records-heading",
      },
      {
        name: "Provenance",
        status: "Evidence-grounded",
        detail: "Stored source labels and exact source-version identifiers remain attached to records when supplied.",
        href: "/#reading-guide",
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
        status: "Partially available",
        detail: "Owners can record supports, contradicts, contextualizes, or derived-from links; members can read them.",
        href: "/relationships",
      },
      {
        name: "Evidence links",
        status: "Partially available",
        detail: "Explicit links connect two records in one workspace. Owners write; members read.",
        href: "/relationships",
      },
      {
        name: "Rationale",
        status: "Partially available",
        detail: "An owner supplies and maintains the rationale for each recorded relationship.",
        href: "/relationships",
      },
      {
        name: "Review basis",
        status: "Partially available",
        detail: "An owner records the provenance or review basis; it is not generated automatically.",
        href: "/relationships",
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
        href: "/#reading-guide",
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
        href: "/#reading-guide",
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
        href: "/#verification",
      },
      {
        name: "Hash verification",
        status: "Connected",
        detail: "The service verifies source bytes against the pinned SHA-256 before delivery and returns a Content-Digest header.",
        href: "/#verification",
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
        href: "/cost-performance",
      },
      {
        name: "Comparisons",
        status: "Historical",
        detail: "Available comparisons are historical benchmark results over fixed datasets.",
        href: "/cost-performance",
      },
    ],
  },
  {
    name: "Cost & Performance",
    features: [
      {
        name: "Historical estimates",
        status: "Historical",
        detail: "Published reports contain experiment-level estimates and measurements with stated limits.",
        href: "/cost-performance",
      },
      {
        name: "Token usage",
        status: "Historical",
        detail: "Token counts are from benchmark runs; they are not per-workspace usage data.",
        href: "/cost-performance",
      },
      {
        name: "API calls",
        status: "Historical",
        detail: "Reported call counts belong to fixed benchmark experiments, not user accounts.",
        href: "/cost-performance",
      },
      {
        name: "Latency",
        status: "Historical",
        detail: "Latency values are benchmark measurements and are not live workspace telemetry.",
        href: "/cost-performance",
      },
    ],
  },
  {
    name: "Settings & Access",
    features: [
      {
        name: "Account profile",
        status: "Limited / Read only",
        detail: "The current account email is displayed; profile editing is not available.",
        href: "/workspace#workspace-session",
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
        href: "/workspace#session-management",
      },
      {
        name: "Personal API Keys",
        status: "Pending",
        detail: "Planned as separate personal credentials. No key issuance or revocation exists; API keys must remain separate from session JWTs.",
        href: "/#settings-access",
      },
    ],
  },
];
