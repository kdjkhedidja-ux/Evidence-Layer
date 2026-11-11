/**
 * Keep the export UI hidden until the export migration, service routes, and BFF
 * are deployed to the target environment. Set the public flag to exactly "true"
 * only after an export download smoke passes.
 */
export function accountExportEnabled({ flag }: { flag?: string }): boolean {
  return flag === "true";
}
