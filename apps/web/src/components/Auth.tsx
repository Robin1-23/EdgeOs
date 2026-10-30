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
import { createContext, useContext, useEffect, useRef, useState } from "react";
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
    <dialog ref={ref} aria-labelledby="signin-title" className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-lg border border-line bg-background p-5 text-foreground backdrop:bg-black/50">
      <h2 id="signin-title" className="text-lg font-semibold">Sign in to EdgeOS</h2>
      <p className="mt-1 text-sm text-muted">Signing in gives you a free daily allowance of questions in the ask box.</p>
      <button type="button" onClick={google} className="mt-4 w-full rounded-lg border border-line px-4 py-2 text-sm font-medium hover:bg-panel">
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
          className="w-full rounded border border-line bg-background px-3 py-2 text-sm"
        />
        <button type="submit" className="w-full rounded-lg bg-accent px-4 py-2 text-sm font-medium text-background">Email me a link</button>
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
      <button type="button" onClick={openSignIn} className="text-muted hover:text-foreground">
        Sign in
      </button>
    );
  }
  return (
    <span className="flex items-center gap-2">
      <span className="hidden max-w-[12rem] truncate text-xs text-muted sm:inline" title={user.email ?? undefined}>
        {user.email ?? user.displayName}
      </span>
      <button type="button" onClick={() => signOut(firebaseAuth()!)} className="text-muted hover:text-foreground">
        Sign out
      </button>
    </span>
  );
}
