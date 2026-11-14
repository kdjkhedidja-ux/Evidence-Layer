import Link from "next/link";
import SiteFooter from "../../../components/site-footer";
import SiteHeader from "../../../components/site-header";
import AuthErrorMessage from "./auth-error-message";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function AuthErrorPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const reason = typeof params.reason === "string" ? params.reason : undefined;
  return (
    <main className="auth-page">
      <SiteHeader />
      <section className="auth-card" aria-labelledby="auth-error-title">
        <p className="eyebrow">ACCOUNT ACCESS</p>
        <AuthErrorMessage initialReason={reason} />
        <nav className="auth-links" aria-label="Recovery options">
          <Link href="/signup">Return to sign-up</Link>
          <Link href="/forgot-password">Request a password-reset link</Link>
          <Link href="/login">Return to sign in</Link>
        </nav>
      </section>
      <SiteFooter />
    </main>
  );
}
