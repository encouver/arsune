// Numbers, sentences, rows and dot stages for the race views. Pure: no React, no DOM.

import { claimGroup, claimPage, type ClaimText } from "../claim-text";
import { usdc } from "./format";
import type { Agent, HunterStat } from "./types";

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
  paying: number;
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
    paying: 0,
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
    else if (group === "proven")
      // Proven and decided, but the payout is not complete until the claim is "paid".
      c.status === "paid" ? counts.paid++ : counts.paying++;
    else if (group === "false") counts.lost++;
    else if (group === "repeat") counts.repeat++;
    else counts.unchecked++;
  }
  return counts;
}

/** The rare results in one small line; "" when there are none. */
export function rareText(counts: LineCounts): string {
  const parts: string[] = [];
  if (counts.paying) parts.push(`${counts.paying} proven, payment on the way`);
  const bondBack: string[] = [];
  if (counts.unchecked)
    bondBack.push(`${counts.unchecked} could not be checked`);
  if (counts.unfunded)
    bondBack.push(`${counts.unfunded} proven, but the budget ran out`);
  if (bondBack.length) parts.push(...bondBack, "deposit back");
  return parts.join(" · ");
}

/** The claims of one race: made since it started. */
export function claimsOfRace<T extends LineClaim>(
  claims: T[],
  startedAt: string,
): T[] {
  const since = Date.parse(startedAt);
  return claims.filter((c) => Date.parse(c.created_at ?? "") >= since);
}

/** The ledger rows that belong to the given claims. */
export function ledgerOfClaims<T extends LineLedgerRow>(
  ledger: T[],
  claims: LineClaim[],
): T[] {
  const ids = new Set(claims.map((c) => c.id));
  return ledger.filter((r) => r.claim_id && ids.has(r.claim_id));
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
  const p = claimPage(c);
  const on = p && p !== "—" ? ` on ${p}` : "";
  const stage = stageOf(c);
  let text: string;
  if (stage === "paid")
    text = paidSentence(c, ledger, nameOf, `${who} proved ${title}${on}`);
  else if (stage === "lost")
    text = `${who} reported ${title}${on} → it was fake, deposit lost`;
  else if (stage === "repeat")
    text = `${who} sent a bug that was already proven → deposit back`;
  else if (c.status === "unfunded")
    text = `${who} proved ${title}, but the budget ran out → deposit back`;
  else text = `${who}: Arsune could not check ${title} → deposit back`;
  return { claimId: c.id, text };
}

// "1 bug", "3 bugs"
function counted(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The running race in one line. */
export function runningStory(agents: number, site: string): string {
  return `${counted(agents, "agent searches", "agents search")} ${site}. Each report needs a deposit. Arsune checks it in a clean browser.`;
}

function uncheckedSentence(n: number): string {
  return `${n} could not be checked: ${n === 1 ? "its deposit" : "their deposits"} came back.`;
}

/** The finished race as sentences. The false-claim sentences are left out when none lost a bond. */
export function finishedStory(counts: LineCounts): string {
  const parts = [
    `${counted(counts.paid, "bug", "bugs")} proven and paid (${usdc(counts.paidAmount)} USDC).`,
  ];
  if (counts.lost)
    parts.push(
      `${counted(counts.lost, "fake report lost its", "fake reports lost their")} deposit.`,
      "Fake reports cost the owner nothing.",
    );
  if (counts.unchecked) parts.push(uncheckedSentence(counts.unchecked));
  return parts.join(" ");
}

/** The owner's outcome: null until a claim has a result. A sentence whose count is 0 is left out. */
export function resultStory(counts: LineCounts): string | null {
  const proven = counts.paid + counts.paying + counts.unfunded;
  if (proven + counts.lost + counts.repeat + counts.unchecked === 0)
    return null;
  const parts = [`Your hunt found ${counted(proven, "bug", "bugs")}.`];
  if (counts.paid)
    parts.push(
      `${counted(counts.paid, "is", "are")} paid (${usdc(counts.paidAmount)} USDC).`,
    );
  if (counts.paying)
    parts.push(
      `${counted(counts.paying, "payment is", "payments are")} on the way.`,
    );
  // An unfunded claim is never paid later: its bond is refunded and the fault stays open.
  if (counts.unfunded)
    parts.push(
      `${counted(counts.unfunded, "was", "were")} not paid: the budget ran out.`,
    );
  if (counts.lost)
    parts.push(
      `${counted(counts.lost, "fake report cost you nothing: its deposit", "fake reports cost you nothing: their deposits")} went into your budget.`,
    );
  if (counts.repeat)
    parts.push(
      `${counted(counts.repeat, "repeat got its", "repeats got their")} deposit back.`,
    );
  if (counts.unchecked) parts.push(uncheckedSentence(counts.unchecked));
  return parts.join(" ");
}

/**
 * Money from Arsune's records for the agents it lists. The race host's counters stop when
 * its runner stops, so they are kept only for agents that the API does not list.
 */
export function withRecords(agents: Agent[], hunters: HunterStat[]): Agent[] {
  const records = new Map(hunters.map((h) => [h.account, h]));
  return agents.map((a) => {
    const h = records.get(a.account);
    return h
      ? { ...a, earned: h.earned + h.scout_earned, bonds_lost: h.bonds_lost }
      : a;
  });
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
