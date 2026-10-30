// Firebase Auth for the browser (sign-in only). The web config is public by design; the API verifies every ID token.
import { getApps, initializeApp } from "firebase/app";
import { type Auth, connectAuthEmulator, getAuth } from "firebase/auth";

const config = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
};

export const authConfigured = Boolean(config.apiKey && config.projectId);

let auth: Auth | null = null;

/** The Auth instance in the browser, or null when sign-in is not configured (or on the server). */
export function firebaseAuth(): Auth | null {
  if (!authConfigured || typeof window === "undefined") return null;
  if (!auth) {
    auth = getAuth(getApps()[0] ?? initializeApp(config));
    // local development only: NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR=http://127.0.0.1:9099
    const emulator = process.env.NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR;
    if (emulator) connectAuthEmulator(auth, emulator, { disableWarnings: true });
  }
  return auth;
}

export const EMAIL_KEY = "edgeos.emailForSignIn"; // the address a sign-in link was sent to, on this device only
