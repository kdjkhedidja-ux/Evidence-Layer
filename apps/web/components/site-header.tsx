"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import ThemeToggle from "../app/theme-toggle";
import NavIcon, { type IconName } from "./nav-icon";
import { createSupabaseBrowserClient } from "../lib/supabase/client";

const NAV_GROUPS: readonly {
  label: string;
  items: readonly { href: string; label: string; icon: IconName }[];
}[] = [
  {
    label: "Navigation",
    items: [
      { href: "/workspace", label: "Workspace", icon: "home" },
      { href: "/workspace/telemetry", label: "Telemetry", icon: "chart" },
      { href: "/#explorer", label: "Evidence", icon: "layers" },
      { href: "/relationships", label: "Relationships", icon: "link" },
      { href: "/workspace#agent-connection", label: "Connect an agent", icon: "plug" },
    ],
  },
  {
    label: "Resources",
    items: [
      { href: "/documentation", label: "Documentation", icon: "report" },
      { href: "/evaluation", label: "Evaluation · Benchmarks", icon: "gauge" },
      { href: "/cost-performance", label: "Cost & Performance", icon: "coins" },
    ],
  },
  {
    label: "User account",
    items: [{ href: "/account", label: "Account", icon: "settings" }],
  },
];

function isCurrent(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (href === "/#explorer") return pathname === "/";
  if (href.includes("#")) return false;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function SiteHeader({ accountEmail }: { accountEmail?: string | null }) {
  const [pathname, setPathname] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(accountEmail ?? null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    setPathname(window.location.pathname);
  }, []);

  useEffect(() => {
    if (pathname) setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (accountEmail !== undefined) {
      setEmail(accountEmail);
      return;
    }
    const currentPath = window.location.pathname;
    if (/^\/(login|signup|forgot-password|reset-password|auth)(\/|$)/.test(currentPath)) return;
    let cancelled = false;
    try {
      void createSupabaseBrowserClient().auth.getSession().then(({ data }) => {
        if (!cancelled) setEmail(data.session?.user.email ?? null);
      }).catch(() => undefined);
    } catch {
      // The header still works as a signed-out navigation menu.
    }
    return () => {
      cancelled = true;
    };
  }, [accountEmail]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      toggleRef.current?.focus();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  async function signOut() {
    setPending(true);
    setMessage("");
    try {
      const { error } = await createSupabaseBrowserClient().auth.signOut({ scope: "local" });
      if (error) {
        setMessage("Sign out could not be confirmed.");
        return;
      }
      window.location.assign("/login?reason=signed-out");
    } catch {
      setMessage("Sign out could not be confirmed.");
    } finally {
      setPending(false);
    }
  }

  // Only the most specific matching destination is marked current, so
  // /workspace/telemetry highlights Telemetry rather than Workspace too.
  const currentHref = NAV_GROUPS.flatMap((group) => group.items)
    .filter((item) => isCurrent(pathname, item.href))
    .reduce<string | null>(
      (best, item) => (best === null || item.href.length > best.length ? item.href : best),
      null,
    );

  return (
    <aside className="site-header" aria-label="Site">
      <div className="site-header-bar">
        <Link className="site-brand" href="/" aria-label="Evidence Layer home">
          <span className="site-brand-mark" aria-hidden="true"><NavIcon name="layers" /></span>
          <span className="site-brand-name">evidence<span>layer</span></span>
        </Link>
        <div className="site-header-actions">
          <ThemeToggle />
          <button
            ref={toggleRef}
            type="button"
            className="site-menu-toggle"
            aria-label={menuOpen ? "Close" : "Menu"}
            aria-expanded={menuOpen}
            aria-controls="site-menu"
            onClick={() => setMenuOpen((value) => !value)}
          >
            <svg
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeWidth="1.8"
              viewBox="0 0 24 24"
            >
              <circle cx="12" cy="12" r="9.5" />
              {menuOpen ? <path d="m9 9 6 6m0-6-6 6" /> : <path d="M8 9h8M8 12h8M8 15h8" />}
            </svg>
          </button>
        </div>
      </div>
      <div className={`site-menu${menuOpen ? " site-menu--open" : ""}`} id="site-menu">
        <nav aria-label="Main navigation">
          {NAV_GROUPS.map((group) => (
            <div className="site-nav-group" key={group.label}>
              <span className="site-eyebrow" aria-hidden="true">{group.label}</span>
              {group.items.map((item) => (
                <Link
                  aria-current={item.href === currentHref ? "page" : undefined}
                  href={item.href}
                  key={item.href}
                  onClick={() => setMenuOpen(false)}
                >
                  <span className="site-navicon"><NavIcon name={item.icon} /></span>
                  {item.label}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="site-side-bottom">
          <div className="site-session">
            {email ? (
              <>
                <span className="site-avatar" aria-hidden="true">{email.slice(0, 1).toUpperCase()}</span>
                <span className="site-session-email">{email}</span>
                <button type="button" onClick={() => void signOut()} disabled={pending}>
                  {pending ? "Signing out…" : "Sign out"}
                </button>
              </>
            ) : (
              <Link href="/login" onClick={() => setMenuOpen(false)}>Sign in</Link>
            )}
          </div>
          {message && <p className="site-menu-error" role="alert">{message}</p>}
        </div>
      </div>
    </aside>
  );
}
