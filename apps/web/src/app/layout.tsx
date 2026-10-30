import type { Metadata } from "next";
import { Geist_Mono, Inter, Space_Grotesk } from "next/font/google";
import Link from "next/link";
import { AccountMenu, AuthProvider } from "@/components/Auth";
import { Logo } from "@/components/Icons";
import { DISCLAIMER } from "@/components/Notices";
import "./globals.css";

const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const spaceGrotesk = Space_Grotesk({ variable: "--font-space-grotesk", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "EdgeOS: Indian stock and fund research", template: "%s · EdgeOS" },
  description: "Descriptive research on NSE-listed companies and Indian mutual funds: every number with its formula and source.",
};

const NAV = [
  { href: "/#markets", label: "Markets" },
  { href: "/tools/overlap", label: "Fund overlap" },
  { href: "/pricing", label: "Pricing" },
];

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${inter.variable} ${spaceGrotesk.variable} ${geistMono.variable} antialiased font-sans min-h-dvh flex flex-col`}>
        <AuthProvider>
          <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2">
            Skip to content
          </a>
          <header className="sticky top-0 z-30 border-b border-line bg-background/70 backdrop-blur-xl">
            <div className="mx-auto grid h-16 max-w-7xl grid-cols-[1fr_auto] items-center gap-4 px-5 md:grid-cols-[1fr_auto_1fr]">
              <Link href="/" className="flex items-center gap-2.5">
                <Logo />
                <span className="font-display text-[17px] font-semibold tracking-tight">EdgeOS</span>
              </Link>
              <nav className="hidden items-center gap-1 text-sm md:flex">
                {NAV.map((n) => (
                  <Link key={n.href} href={n.href} className="rounded-full px-3.5 py-2 text-muted transition-colors hover:text-foreground">
                    {n.label}
                  </Link>
                ))}
              </nav>
              <div className="flex items-center justify-end gap-2 text-sm">
                <AccountMenu />
                <Link href="/ask" className="btn-primary px-4 py-2 text-sm">Ask EdgeOS</Link>
              </div>
            </div>
          </header>
          <main id="main" className="page-backdrop flex-1">
            <div className="mx-auto w-full max-w-7xl px-5 pb-20 pt-10">{children}</div>
          </main>
          <footer className="border-t border-line">
            <div className="mx-auto flex max-w-7xl flex-col gap-6 px-5 py-10 text-xs text-muted md:flex-row md:justify-between">
              <div className="max-w-xl space-y-3">
                <Link href="/" className="flex items-center gap-2 text-foreground">
                  <Logo className="h-5 w-5" />
                  <span className="font-display text-sm font-semibold">EdgeOS</span>
                </Link>
                <p className="leading-relaxed">{DISCLAIMER}</p>
              </div>
              <nav className="flex gap-6">
                <Link href="/ask" className="hover:text-foreground">Ask</Link>
                <Link href="/tools/overlap" className="hover:text-foreground">Fund overlap</Link>
                <Link href="/pricing" className="hover:text-foreground">Pricing</Link>
                <Link href="/account" className="hover:text-foreground">Account</Link>
              </nav>
            </div>
          </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
