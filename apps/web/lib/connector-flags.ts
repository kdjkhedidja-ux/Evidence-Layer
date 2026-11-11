/** Google Drive stays visibly "coming soon" until OAuth verification and deployment. */
export function googleDriveConnectorEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}
