"use client";

import {
  GoogleAuthProvider,
  isSignInWithEmailLink,
  onAuthStateChanged,
  sendSignInLinkToEmail,
  signInWithEmailLink,
  signInWithPopup,
  signOut,
  type User,
} from "firebase/auth";
import Link from "next/link";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icons";
import { EMAIL_KEY, authConfigured, firebaseAuth } from "@/lib/firebase";

type AuthState = { configured: boolean; ready: boolean; user: User | null; openSignIn: () => void };
const AuthContext = createContext<AuthState>({ configured: false, ready: true, user: null, openSignIn: () => {} });

export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(!authConfigured);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const auth = firebaseAuth();
    if (!auth) return;
    // finishing an email-link sign-in: the link opened this page
    if (isSignInWithEmailLink(auth, window.location.href)) {
      const email = window.localStorage.getItem(EMAIL_KEY) ?? window.prompt("Confirm the email address the sign-in link was sent to");
      if (email) {
        signInWithEmailLink(auth, email, window.location.href)
          .then(() => {
            window.localStorage.removeItem(EMAIL_KEY);
            window.history.replaceState(null, "", window.location.pathname);
          })
          .catch(() => {});
      }
    }
    return onAuthStateChanged(auth, (u) => {
      setUser(u);
      setReady(true);
    });
  }, []);

  return (
    <AuthContext.Provider value={{ configured: authConfigured, ready, user, openSignIn: () => dialog.current?.showModal() }}>
      {children}
      {authConfigured && <SignInDialog ref={dialog} />}
    </AuthContext.Provider>
  );
}

function SignInDialog({ ref }: { ref: React.RefObject<HTMLDialogElement | null> }) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const close = () => {
    ref.current?.close();
    setStatus(null);
  };

  async function google() {
    try {
      await signInWithPopup(firebaseAuth()!, new GoogleAuthProvider());
      close();
    } catch (e) {
      setStatus(`Google sign-in did not complete (${(e as { code?: string }).code ?? "error"}).`);
    }
  }

  async function emailLink(ev: React.FormEvent) {
    ev.preventDefault();
    try {
      await sendSignInLinkToEmail(firebaseAuth()!, email, { url: window.location.href, handleCodeInApp: true });
      window.localStorage.setItem(EMAIL_KEY, email);
      setStatus(`We sent a sign-in link to ${email}. Open it on this device to finish signing in.`);
    } catch (e) {
      setStatus(`The link could not be sent (${(e as { code?: string }).code ?? "error"}).`);
    }
  }

  return (
    <dialog ref={ref} aria-labelledby="signin-title" className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-line bg-surface p-6 text-foreground shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm">
      <h2 id="signin-title" className="text-lg font-semibold">Sign in to EdgeOS</h2>
      <p className="mt-1 text-sm text-muted">Signing in gives you a free daily allowance of questions in the ask box.</p>
      <button type="button" onClick={google} className="mt-5 w-full rounded-xl border border-line px-4 py-2.5 text-sm font-medium hover:bg-surface-2">
        Continue with Google
      </button>
      <form onSubmit={emailLink} className="mt-4 space-y-2">
        <label htmlFor="signin-email" className="block text-sm">Or get a sign-in link by email</label>
        <input
          id="signin-email"
          type="email"
          required
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-xl border border-line bg-background px-3 py-2.5 text-sm"
        />
        <button type="submit" className="w-full rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-accent-ink">Email me a link</button>
      </form>
      {status && <p role="status" className="mt-3 text-sm">{status}</p>}
      <button type="button" onClick={close} className="mt-4 text-sm text-muted hover:text-foreground">Close</button>
    </dialog>
  );
}

/** Header control: sign in, or who is signed in and sign out. Hidden when sign-in is not configured. */
export function AccountMenu() {
  const { configured, ready, user, openSignIn } = useAuth();
  if (!configured || !ready) return null;
  if (!user) {
    return (
      <button type="button" onClick={openSignIn} className="rounded-full px-3.5 py-2 text-sm text-muted transition-colors hover:text-foreground">
        Sign in
      </button>
    );
  }
  return (
    <span className="ml-1 flex items-center gap-1">
      <Link href="/account" aria-label="Your account" title={user.email ?? undefined}
            className="flex h-9 w-9 items-center justify-center rounded-full bg-surface-2 text-foreground hover:bg-accent-soft">
        <Icon name="user" />
      </Link>
      <button type="button" onClick={() => signOut(firebaseAuth()!)} className="rounded-full px-3 py-2 text-muted hover:bg-surface-2 hover:text-foreground">
        Sign out
      </button>
    </span>
  );
}
