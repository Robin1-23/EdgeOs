import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { AccountMenu, AuthProvider } from "@/components/Auth";
import { DISCLAIMER } from "@/components/Notices";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "EdgeOS: Indian stock and fund research", template: "%s · EdgeOS" },
  description: "Descriptive research on NSE-listed companies and Indian mutual funds: every number with its formula and source.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable} antialiased font-sans min-h-screen flex flex-col`}>
        <AuthProvider>
        <header className="border-b border-line">
          <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
            <Link href="/" className="font-semibold tracking-tight text-lg">
              EdgeOS
            </Link>
            <nav className="flex items-center gap-4 text-sm">
              <Link href="/ask" className="text-muted hover:text-foreground">
                Ask
              </Link>
              <Link href="/tools/overlap" className="text-muted hover:text-foreground">
                Fund overlap
              </Link>
              <AccountMenu />
            </nav>
          </div>
        </header>
        <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-6">{children}</main>
        <footer className="border-t border-line">
          <p className="mx-auto max-w-6xl px-4 py-4 text-xs text-muted leading-relaxed">{DISCLAIMER}</p>
        </footer>
        </AuthProvider>
      </body>
    </html>
  );
}
