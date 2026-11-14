export type Token = {
  asset_id: string;
  symbol: string;
  blockchain: string;
  decimals: number;
  price: number | null;
  contract_address: string | null;
};

export type SettlementMode = "dry" | "near_intents" | "escrow";

export type Options = {
  settlement_mode: SettlementMode;
  arsune_account: string;
  token: string;
  token_symbol: string;
  token_decimals: number;
  intents_contract: string;
  // Escrow mode only: funding deposits go to this contract.
  escrow_contract: string | null;
  demo_site_url: string;
  demo_site_origin: string;
  simulated_funding: boolean;
  crosschain: { available: boolean; reason: string | null; tokens: Token[] };
};

export type LedgerRow = {
  id: string;
  claim_id?: string | null;
  kind: string;
  amount: number;
  account: string;
  memo: string;
  tx_id: string | null;
  explorer_url: string | null;
  settlement_mode: string;
  status: string;
  created_at: string;
};

export type OwnerClaim = {
  id: string;
  hunter_account: string;
  claim_type: string;
  urls?: string[];
  details?: Record<string, unknown> | null;
  // Equal faults have equal fingerprints; a second claim on one is a duplicate.
  fingerprint?: string | null;
  status: string;
  outcome: string | null;
  checker_reason?: string | null;
  settlement_note?: string | null;
  scout?: { account: string; share_bps: number } | null;
  // Set once the claim is decided: sha256 of the public evidence bundle.
  evidence_hash: string | null;
  evidence_url: string | null;
  created_at?: string;
};

// Arsune's answer to "can the checker load this site?". The worker loads the bounty URL at
// 1280 and 390 pixels; the bounty opens (or resumes) only when the check passes.
export type SiteCheck = {
  job_id: string;
  state: "checking" | "passed" | "failed";
  url: string;
  reason: string | null;
  problems: string[];
  note: string | null;
  checked_at: string | null;
  fresh_until: string | null;
};

// One row of GET /api/bounties.
export type BountySummary = {
  id: string;
  url: string;
  origin: string;
  owner_account: string;
  status: "pending_proof" | "open" | "paused" | "closed" | string;
  // "budget_spent" while a bounty is closed because its budget ran out; a top-up reopens it.
  closed_reason: string | null;
  // Why Arsune paused the bounty: claims that its own limits left unverified.
  paused_reason: string | null;
  reward_amount: number;
  bond_amount: number;
  fee_bps: number;
  fee_amount: number;
  // What is left to pay with; budget_target is what the owner planned to fund.
  budget_amount: number;
  budget_target: number;
  settlement_mode: SettlementMode;
  created_at: string;
};

export type AccessMarker = { index: number; label: string; value: string };

export type AccessTest = {
  ready: boolean;
  login_url: string | null;
  email: string | null;
  password: string | null;
  markers: AccessMarker[];
};

export const ownerKeyName = (bountyId: string) =>
  `arsune-owner-key:${bountyId}`;

export const ownerSiteName = (bountyId: string) =>
  `arsune-owner-site:${bountyId}`;

/** IDs of the hunts that this browser created: it has their owner keys. */
export function localOwnedIds(): string[] {
  const prefix = "arsune-owner-key:";
  const ids: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const name = localStorage.key(i) ?? "";
    if (name.startsWith(prefix)) ids.push(name.slice(prefix.length));
  }
  return ids;
}

// Earlier bounties of this browser on the same site: the server reuses their proof token.
// Keys saved before the site was stored go last; the server checks the origin itself.
export function reusableProofs(siteUrl: string) {
  let site: string;
  try {
    site = new URL(siteUrl).origin;
  } catch {
    return [];
  }
  const prefix = "arsune-owner-key:";
  const same: { bounty_id: string; owner_key: string }[] = [];
  const unknown: { bounty_id: string; owner_key: string }[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const name = localStorage.key(i) ?? "";
    if (!name.startsWith(prefix)) continue;
    const id = name.slice(prefix.length);
    const key = localStorage.getItem(name);
    if (!key) continue;
    const saved = localStorage.getItem(ownerSiteName(id));
    if (saved === site) same.push({ bounty_id: id, owner_key: key });
    else if (saved === null) unknown.push({ bounty_id: id, owner_key: key });
  }
  return [...same, ...unknown].slice(0, 100);
}

export type OwnerBounty = BountySummary & {
  test_account?: {
    login_url: string;
    email: string;
    password: string;
  } | null;
  security_test_ready?: boolean;
  owner_key?: string;
  site_check: SiteCheck | null;
  proof_token: string;
  proof_url: string;
  claims: OwnerClaim[];
  ledger: LedgerRow[];
};

export type FundingRequest = {
  id: string;
  status: string;
  final: boolean;
  oneclick_status: string | null;
  amount: number;
  origin_asset: string;
  origin_symbol: string;
  origin_chain: string;
  origin_decimals: number;
  deposit_address: string;
  deposit_memo: string | null;
  amount_in: string;
  amount_in_formatted: string;
  min_amount_in: string;
  min_amount_in_formatted: string;
  refund_to: string;
  deadline: string;
  address_closes_at: string;
  time_estimate: number | null;
  delivered_amount: number | null;
  note: string | null;
  checked_at: string | null;
  created_at: string;
};

export type FundingList = {
  bounty_id: string;
  bounty_status: string;
  budget_amount: number;
  budget_target: number;
  settlement_mode: string;
  requests: FundingRequest[];
  status_error: string | null;
};

export const DEFAULT_FEE_BPS = 1000;

// The escrow is not deployed on mainnet, so its badge does not promise real money.
const SETTLEMENT_BADGES: Record<SettlementMode, string | null> = {
  dry: null,
  near_intents: "Real settlement via NEAR Intents",
  escrow: "Settlement by the escrow contract",
};

/** The header badge for a mode that is not dry; dry pages show the simulation tag instead. */
export function settlementBadge(mode: SettlementMode): string | null {
  return SETTLEMENT_BADGES[mode];
}

export function shortHash(hash: string): string {
  return `${hash.slice(0, 12)}…`;
}
// Same rule as the API: NEAR account IDs, named or 64-hex implicit.
export const ACCOUNT =
  /^(?=.{2,64}$)([a-z\d]+[-_])*[a-z\d]+(\.([a-z\d]+[-_])*[a-z\d]+)*$/;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail
      .map((d) => {
        const field = Array.isArray(d?.loc) ? d.loc[d.loc.length - 1] : "";
        return field ? `${field}: ${d?.msg}` : String(d?.msg ?? "");
      })
      .join(" ");
  return null;
}

export async function api<T>(
  path: string,
  body?: unknown,
  extra: { method?: string; headers?: Record<string, string> } = {},
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: extra.method ?? (body === undefined ? "GET" : "POST"),
      headers: {
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...extra.headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "The Arsune API cannot be reached.");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok)
    throw new ApiError(
      res.status,
      detailText(data?.detail) ?? `The Arsune API answered HTTP ${res.status}.`,
    );
  return data as T;
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** "12.5" -> 12500000 (USDC has 6 decimals). Null when the text is not an amount. */
export function parseUsdc(text: string): number | null {
  const m = /^\s*(\d{1,9})(?:\.(\d{0,6}))?\s*$/.exec(text);
  if (!m) return null;
  return Number(m[1]) * 1_000_000 + Number((m[2] ?? "").padEnd(6, "0"));
}

export function usdc(units: number): string {
  const text = (units / 1e6).toFixed(6).replace(/0+$/, "");
  const [whole, frac = ""] = text.split(".");
  return `${whole}.${frac.padEnd(2, "0")}`;
}

const BOUNTY_LABELS: Record<string, string> = {
  pending_proof: "Waiting for proof",
  open: "Open",
  paused: "Paused",
  closed: "Closed",
};

export function bountyLabel(b: {
  status: string;
  closed_reason: string | null;
}) {
  if (b.status === "closed" && b.closed_reason === "budget_spent")
    return "Budget spent";
  return BOUNTY_LABELS[b.status] ?? b.status;
}

/** "staging.example.com/" for a bounty URL. */
export function siteName(url: string): string {
  return url.replace(/^https?:\/\//, "");
}

/** What a proven bug costs the owner: the reward and Arsune's fee. */
export function perBug(b: { reward_amount: number; fee_amount: number }) {
  return b.reward_amount + b.fee_amount;
}

/** All completed funding, not what is left after payouts. */
export function fundedTotal(b: { budget_amount: number; ledger: LedgerRow[] }) {
  const funds = b.ledger
    .filter((r) => r.kind === "fund" && r.status === "completed")
    .reduce((sum, r) => sum + r.amount, 0);
  return Math.max(funds, b.budget_amount);
}

export function fundedFraction(b: {
  budget_amount: number;
  budget_target: number;
}) {
  return b.budget_target > 0
    ? Math.min(1, b.budget_amount / b.budget_target)
    : 0;
}

export function untilText(iso: string, now: number): string {
  const ms = new Date(iso).getTime() - now;
  if (Number.isNaN(ms)) return "";
  if (ms <= 0) return "passed";
  const minutes = Math.round(ms / 60_000);
  if (minutes < 120) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `in ${hours} h` : `in ${Math.round(hours / 24)} days`;
}

export function utcTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
