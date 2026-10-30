import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { AccountMenu, AuthProvider } from "@/components/Auth";
import { Logo } from "@/components/Icons";
import { DISCLAIMER } from "@/components/Notices";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "EdgeOS: Indian stock and fund research", template: "%s · EdgeOS" },
  description: "Descriptive research on NSE-listed companies and Indian mutual funds: every number with its formula and source.",
};

const NAV = [
  { href: "/ask", label: "Ask" },
  { href: "/tools/overlap", label: "Fund overlap" },
  { href: "/pricing", label: "Plans" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased font-sans min-h-dvh flex flex-col`}>
        <AuthProvider>
          <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
            Skip to content
          </a>
          <header className="sticky top-0 z-30 border-b border-line/70 bg-background/75 backdrop-blur-xl">
            <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4">
              <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
                <Logo />
                <span className="text-lg">EdgeOS</span>
              </Link>
              <nav className="flex items-center gap-1 text-sm">
                {NAV.map((n) => (
                  <Link
                    key={n.href}
                    href={n.href}
                    className={`rounded-full px-3 py-2 text-muted transition-colors hover:bg-surface-2 hover:text-foreground ${n.href === "/pricing" ? "hidden sm:block" : ""}`}
                  >
                    {n.label}
                  </Link>
                ))}
                <AccountMenu />
              </nav>
            </div>
          </header>
          <main id="main" className="page-glow flex-1">
            <div className="mx-auto w-full max-w-6xl px-4 pb-16 pt-8">{children}</div>
          </main>
          <footer className="border-t border-line">
            <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-6 text-xs text-muted sm:flex-row sm:items-start sm:justify-between">
              <p className="max-w-3xl leading-relaxed">{DISCLAIMER}</p>
              <nav className="flex shrink-0 gap-4">
                <Link href="/pricing" className="hover:text-foreground">Plans</Link>
                <Link href="/account" className="hover:text-foreground">Account</Link>
              </nav>
            </div>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
