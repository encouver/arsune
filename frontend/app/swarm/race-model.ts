// Numbers, sentences, rows and dot stages for the race views. Pure: no React, no DOM.

import { claimGroup, claimPage, type ClaimText } from "../claim-text";
import { usdc } from "./format";
import type { Agent } from "./types";

export const DOT_CAP = 12;

export type LineClaim = ClaimText & {
  id: string;
  hunter_account: string;
  fingerprint?: string | null;
  scout?: { account: string } | null;
  created_at?: string;
  updated_at?: string;
};

export type LineLedgerRow = {
  kind: string;
  amount: number;
  account: string;
  status?: string;
  claim_id?: string | null;
};

export type LineCounts = {
  claims: number;
  checking: number;
  paid: number;
  lost: number;
  repeat: number;
  unchecked: number;
  unfunded: number;
  paidAmount: number;
};

export type Pool = { into: number; left: number; fraction: number | null };
export type Stage = "claim" | "check" | "paid" | "lost" | "repeat" | "other";
export type Result = "earned" | "lost" | "broke" | "none";
export type NameOf = (account: string) => string;

function completed(ledger: LineLedgerRow[], kind: string): LineLedgerRow[] {
  return ledger.filter((r) => r.kind === kind && r.status === "completed");
}

function total(ledger: LineLedgerRow[], kind: string): number {
  return completed(ledger, kind).reduce((sum, r) => sum + r.amount, 0);
}

export function lineCounts(
  claims: LineClaim[],
  ledger: LineLedgerRow[],
): LineCounts {
  const counts: LineCounts = {
    claims: claims.length,
    checking: 0,
    paid: 0,
    lost: 0,
    repeat: 0,
    unchecked: 0,
    unfunded: 0,
    paidAmount: total(ledger, "reward_out") + total(ledger, "scout_share"),
  };
  for (const c of claims) {
    const group = claimGroup(c);
    if (c.status === "unfunded") counts.unfunded++;
    else if (group === "pending") counts.checking++;
    else if (group === "proven") counts.paid++;
    else if (group === "false") counts.lost++;
    else if (group === "repeat") counts.repeat++;
    else counts.unchecked++;
  }
  return counts;
}

/** The rare results in one small line; "" when there are none. */
export function rareText(counts: LineCounts): string {
  const parts: string[] = [];
  if (counts.unchecked) parts.push(`${counts.unchecked} could not be checked`);
  if (counts.unfunded)
    parts.push(`${counts.unfunded} proven, but the pool was empty`);
  return parts.length ? [...parts, "bond back"].join(" · ") : "";
}

// A lost bond goes into the pool too, so budget_amount can grow past the funded amount.
export function poolOf(budgetAmount: number, ledger: LineLedgerRow[]): Pool {
  const into = total(ledger, "fund") + total(ledger, "bond_forfeit");
  const fraction =
    into <= 0 ? null : Math.min(1, Math.max(0, budgetAmount / into));
  return { into, left: budgetAmount, fraction };
}

export function stageOf(c: LineClaim): Stage {
  if (c.status === "received") return "claim";
  const group = claimGroup(c);
  if (group === "pending") return "check";
  if (c.status === "unfunded") return "other";
  if (group === "proven") return "paid";
  if (group === "false") return "lost";
  if (group === "repeat") return "repeat";
  return "other";
}

function changedAt(c: LineClaim): string {
  return c.updated_at ?? c.created_at ?? "";
}

function paidSentence(
  c: LineClaim,
  ledger: LineLedgerRow[],
  nameOf: NameOf,
  head: string,
): string {
  const reward = completed(ledger, "reward_out").find(
    (r) => r.claim_id === c.id && r.account === c.hunter_account,
  );
  if (!reward) return `${head} → proven, payment pending`;
  const share = completed(ledger, "scout_share").find(
    (r) => r.claim_id === c.id,
  );
  const scout = share
    ? ` · ${nameOf(share.account)} +${usdc(share.amount)}`
    : "";
  return `${head} → paid +${usdc(reward.amount)} USDC${scout}`;
}

/** One plain sentence for the newest claim with a result. */
export function latestSentence(
  claims: LineClaim[],
  ledger: LineLedgerRow[],
  nameOf: NameOf,
  titleOf: (c: LineClaim) => string,
): { claimId: string; text: string } | null {
  let newest: LineClaim | null = null;
  for (const c of claims)
    if (
      claimGroup(c) !== "pending" &&
      (!newest || changedAt(c) > changedAt(newest))
    )
      newest = c;
  if (!newest) return null;
  const c = newest;
  const who = nameOf(c.hunter_account);
  const title = `"${titleOf(c)}"`;
  const page = claimPage(c);
  const stage = stageOf(c);
  let text: string;
  if (stage === "paid")
    text = paidSentence(c, ledger, nameOf, `${who} proved ${title} on ${page}`);
  else if (stage === "lost")
    text = `${who} claimed ${title} on ${page} → it was not true, bond lost`;
  else if (stage === "repeat")
    text = `${who} sent a bug that was already proven → bond back`;
  else if (c.status === "unfunded")
    text = `${who} proved ${title}, but the pool was empty → bond back`;
  else text = `${who}: Arsune could not check ${title} → bond back`;
  return { claimId: c.id, text };
}

export function resultOf(a: Agent): Result {
  if (a.state === "broke") return "broke";
  if (a.bonds_lost > a.earned) return "lost";
  if (a.earned > 0) return "earned";
  return "none";
}

export function net(a: Agent): number {
  return a.earned - a.bonds_lost;
}

export function signedUsdc(units: number): string {
  if (units > 0) return `+${usdc(units)}`;
  if (units < 0) return `−${usdc(-units)}`;
  return usdc(0);
}

export function agentRows(agents: Agent[]): Record<Result, Agent[]> {
  const rows: Record<Result, Agent[]> = {
    earned: [],
    lost: [],
    broke: [],
    none: [],
  };
  for (const a of agents) rows[resultOf(a)].push(a);
  for (const list of Object.values(rows))
    list.sort(
      (a, b) =>
        Math.abs(net(b)) - Math.abs(net(a)) ||
        (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
    );
  return rows;
}

function strategyText(a: Agent): string {
  return a.strategy === "ai" || a.strategy === "ai-scout" ? "AI" : a.strategy;
}

/** "hunter-045 · mobile · trusted · +2.14 USDC"; fallback agents have no strategy. */
export function agentLabel(a: Agent, fallback: boolean): string {
  const money = `${signedUsdc(net(a))} USDC`;
  if (fallback) return [a.id, "tier not reported", money].join(" · ");
  const parts = [a.id, strategyText(a)];
  if (a.role !== "scout" && a.tier) parts.push(a.tier.replaceAll("_", " "));
  return [...parts, money].join(" · ");
}

export function isWorking(a: Agent): boolean {
  return ["searching", "negotiating", "claiming", "waiting"].includes(a.state);
}

export function claimsOfAgent<T extends LineClaim>(
  claims: T[],
  account: string,
  role: "hunter" | "scout",
): T[] {
  return claims.filter((c) =>
    role === "hunter"
      ? c.hunter_account === account
      : c.scout?.account === account,
  );
}

/** Claims that get a dot: new since the last poll, oldest first, at most `free`. */
export function pickNewDots(
  seen: Set<string> | null,
  claims: LineClaim[],
  free: number,
): { seen: Set<string>; fresh: string[] } {
  const all = new Set(claims.map((c) => c.id));
  if (seen === null) return { seen: all, fresh: [] };
  const fresh = claims
    .filter((c) => !seen.has(c.id))
    .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""))
    .slice(0, Math.max(0, free))
    .map((c) => c.id);
  return { seen: new Set([...seen, ...all]), fresh };
}
