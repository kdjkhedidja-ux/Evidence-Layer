/** Google Drive stays visibly "coming soon" until OAuth verification and deployment. */
export function googleDriveConnectorEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}

/** Notion stays coming soon until its OAuth connection and deployment are verified. */
export function notionConnectorEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}

/** GitHub stays coming soon until its App installation and deployment are verified. */
export function githubConnectorEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}
