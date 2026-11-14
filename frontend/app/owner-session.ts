// Sign in with NEAR: the owner's wallet signs a one-time Arsune message (NEP-413), and the API
// answers with a 7-day session. With it, the owner sees and runs their hunts in any browser.
import { api, ApiError } from "./bounties/api";
import { connectWallet, ed25519Text } from "./bounties/wallet";

export type OwnerSession = {
  account_id: string;
  session: string;
  expires_at: number; // Unix seconds
};

const STORAGE = "arsune-owner-session";
// Components listen for this event to update after sign in or sign out.
export const SESSION_EVENT = "arsune-owner-session";

export function ownerSession(): OwnerSession | null {
  if (typeof localStorage === "undefined") return null;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE) ?? "null");
    if (saved && saved.expires_at * 1000 > Date.now()) return saved;
  } catch {
    // A damaged value counts as signed out.
  }
  return null;
}

function save(value: OwnerSession | null) {
  if (value) localStorage.setItem(STORAGE, JSON.stringify(value));
  else localStorage.removeItem(STORAGE);
  window.dispatchEvent(new Event(SESSION_EVENT));
}

export function signOut() {
  save(null);
}

/** The Authorization header of the session, or no header. */
export function sessionHeaders(): Record<string, string> {
  const s = ownerSession();
  return s ? { Authorization: `Bearer ${s.session}` } : {};
}

/** A 401 means the API did not accept the session: forget it, so the owner signs in again. */
export function forgetSessionOn(e: unknown) {
  if (e instanceof ApiError && e.status === 401 && ownerSession()) signOut();
}

export async function signInWithNear(): Promise<OwnerSession> {
  const account = await connectWallet();
  const challenge = await api<{
    message: string;
    nonce: string;
    recipient: string;
  }>("/api/owner/challenge", {});
  const signed = await account.wallet.signMessage({
    message: challenge.message,
    recipient: challenge.recipient,
    nonce: Uint8Array.from(atob(challenge.nonce), (c) => c.charCodeAt(0)),
    network: "mainnet",
    signerId: account.accountId,
  });
  const session = await api<OwnerSession>("/api/owner/login", {
    account_id: signed.accountId || account.accountId,
    public_key: ed25519Text(signed.publicKey, 32),
    signature: ed25519Text(signed.signature, 64),
    nonce: challenge.nonce,
  });
  save(session);
  return session;
}
