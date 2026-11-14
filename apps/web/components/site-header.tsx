"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { createSupabaseBrowserClient } from "../lib/supabase/client";

const NAV_ITEMS = [
  { href: "/workspace", label: "Workspace" },
  { href: "/workspace/telemetry", label: "Telemetry" },
  { href: "/#explorer", label: "Evidence" },
  { href: "/relationships", label: "Relationships" },
  { href: "/workspace#agent-connection", label: "Connect an agent" },
  { href: "/documentation", label: "Documentation" },
  { href: "/evaluation", label: "Evaluation · Benchmarks" },
  { href: "/cost-performance", label: "Cost & Performance" },
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/data-deletion", label: "Data Deletion" },
] as const;

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
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (accountEmail !== undefined) {
      setEmail(accountEmail);
      return;
    }
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

  return (
    <header className="site-header">
      <Link className="site-brand" href="/" aria-label="Evidence Layer home">
        evidence<span>layer</span>
      </Link>
      <button
        ref={toggleRef}
        type="button"
        className="site-menu-toggle"
        aria-expanded={menuOpen}
        aria-controls="site-menu"
        onClick={() => setMenuOpen((value) => !value)}
      >
        {menuOpen ? "Close" : "Menu"}
      </button>
      <div className={`site-menu${menuOpen ? " site-menu--open" : ""}`} id="site-menu">
        <nav aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <Link
              aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
              href={item.href}
              key={item.href}
              onClick={() => setMenuOpen(false)}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="site-session">
          {email ? (
            <>
              <span>{email}</span>
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
    </header>
  );
}
