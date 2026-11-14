import Link from "next/link";
import PageTitle from "../components/page-title";
import SiteHeader from "../components/site-header";
import type { ReactNode } from "react";

const PUBLIC_LINKS = [
  { href: "/evaluation", label: "Evaluation · Benchmarks" },
  { href: "/cost-performance", label: "Cost & Performance" },
  { href: "/privacy", label: "Privacy Policy" },
  { href: "/terms", label: "Terms of Use" },
  { href: "/data-deletion", label: "Data Deletion" },
] as const;

export function PublicSiteShell({
  currentPath,
  eyebrow,
  title,
  description,
  children,
}: {
  currentPath: string;
  eyebrow: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="public-site">
      <a className="public-skip-link" href="#main-content">Skip to content</a>
      <SiteHeader />

      <main className="public-main" id="main-content">
        <div className="public-hero">
          <p className="public-eyebrow">{eyebrow}</p>
          <h1><PageTitle>{title}</PageTitle></h1>
          <p className="public-lede">{description}</p>
        </div>
        <div className="public-body">{children}</div>
      </main>

      <footer className="public-footer">
        <p>Evidence Layer · Evidence selection with traceable sources.</p>
        <nav aria-label="Legal and product information">
          {PUBLIC_LINKS.map((item) => (
            <Link
              aria-current={currentPath === item.href ? "page" : undefined}
              href={item.href}
              key={item.href}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </footer>
    </div>
  );
}

export function PublicationBlockers({ items }: { items: string[] }) {
  return (
    <aside aria-labelledby="publication-blockers-title" className="public-callout public-callout--notice">
      <p className="public-eyebrow">Publication status</p>
      <h2 id="publication-blockers-title">Complete these details before service launch</h2>
      <p>
        This page is a working publication draft. The bracketed items and the operational facts
        listed here must be confirmed by the service operator before this notice is treated as final.
      </p>
      <ul>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </aside>
  );
}

export function ResponsiveTable({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <>
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need to focus and scroll wide tables on narrow screens. */}
      <section aria-label={label} className="public-table-scroll" tabIndex={0}>
        {children}
      </section>
    </>
  );
}
