import Link from "next/link";

const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/data-deletion", label: "Data Deletion" },
] as const;

export default function SiteFooter({
  currentPath,
  includeDeveloperDocs = false,
}: {
  currentPath?: string;
  includeDeveloperDocs?: boolean;
}) {
  return (
    <footer className="site-footer">
      <nav aria-label="Legal and product information">
        {LEGAL_LINKS.map((link) => (
          <Link
            aria-current={currentPath === link.href ? "page" : undefined}
            href={link.href}
            key={link.href}
          >
            {link.label}
          </Link>
        ))}
        {includeDeveloperDocs && <Link href="/documentation">Developer documentation</Link>}
      </nav>
    </footer>
  );
}
