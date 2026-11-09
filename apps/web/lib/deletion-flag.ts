export type DeletionFlagEnv = {
  flag?: string;
};

/**
 * The individual deletion UI is enabled by default. An explicit
 * NEXT_PUBLIC_DELETION_REQUESTS_ENABLED=false is the emergency kill switch.
 * The Vercel credential available to this project cannot write project env
 * vars, so production enablement must not depend on setting one.
 */
export function deletionRequestsEnabled({ flag }: DeletionFlagEnv): boolean {
  return flag !== "false";
}
