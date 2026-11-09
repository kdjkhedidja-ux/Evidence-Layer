export type DeletionFlagEnv = {
  flag?: string;
  vercelEnv?: string;
};

export function deletionRequestsEnabled({ flag, vercelEnv }: DeletionFlagEnv): boolean {
  if (flag === "true") return true;
  if (flag === undefined && vercelEnv === "production") return true;
  return false;
}
