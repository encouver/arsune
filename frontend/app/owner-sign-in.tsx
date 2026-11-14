"use client";

import { useEffect, useState } from "react";
import { errorText } from "./bounties/api";
import {
  ownerSession,
  OwnerSession,
  SESSION_EVENT,
  signInWithNear,
  signOut,
} from "./owner-session";

/** Hook: the current owner session, updated on sign in and sign out. */
export function useOwnerSession(): OwnerSession | null {
  const [session, setSession] = useState<OwnerSession | null>(null);
  useEffect(() => {
    const update = () => setSession(ownerSession());
    update();
    window.addEventListener(SESSION_EVENT, update);
    window.addEventListener("storage", update);
    return () => {
      window.removeEventListener(SESSION_EVENT, update);
      window.removeEventListener("storage", update);
    };
  }, []);
  return session;
}

export default function OwnerSignIn() {
  const session = useOwnerSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      await signInWithNear();
    } catch (e) {
      setError(errorText(e));
    }
    setBusy(false);
  }

  if (session)
    return (
      <div className="owner-sign-in" data-testid="owner-signed-in">
        <span title="Signed in with NEAR">{session.account_id}</span>
        <button type="button" onClick={signOut}>
          Sign out
        </button>
      </div>
    );
  return (
    <div className="owner-sign-in">
      <button
        type="button"
        onClick={signIn}
        disabled={busy}
        data-testid="owner-sign-in"
      >
        {busy ? "Signing in…" : "Sign in with NEAR"}
      </button>
      {error && (
        <small className="bo-invalid" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}
