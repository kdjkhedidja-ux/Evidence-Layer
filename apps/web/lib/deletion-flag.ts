export type DeletionFlagEnv = {
  flag?: string;
};

/**
 * The individual deletion UI is disabled by default. Set the public flag to
 * exactly "true" only after the deletion worker is deployed, observed healthy,
 * and an authenticated accept-to-purged smoke has passed for the target
 * environment. Any other value keeps the UI hidden.
 */
export function deletionRequestsEnabled({ flag }: DeletionFlagEnv): boolean {
  return flag === "true";
}
