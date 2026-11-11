import type { SettlementMode } from "../bounties/api";

export type AgentState =
  "searching" | "negotiating" | "claiming" | "waiting" | "broke" | "done";

export type Agent = {
  id: string;
  account: string;
  role: "hunter" | "scout";
  strategy: string;
  state: AgentState;
  balance: number;
  claims: number;
  proven: number;
  earned: number;
  bonds_lost: number;
  // Arsune's reputation tier and the bond the hunter must sign now; null for scouts.
  tier?: string | null;
  required_bond?: number | null;
};

export type AiUsage = {
  agents: number;
  calls: number;
  usd_spent: number;
  budget_usd: number;
  model: string;
};

export type Tip = {
  id: string;
  scout: string;
  claim_type: string;
  urls: string[];
  ask_share_bps: number;
  status: "offered" | "countered" | "sold" | "declined";
  buyer: string | null;
  final_share_bps: number | null;
  note: string | null;
};

export type SwarmState = {
  run_id: string;
  mode: "simulation" | "live";
  bounty_id: string | null;
  started_at: string;
  finished: boolean;
  agents: Agent[];
  tips: Tip[];
  log: { at: string; agent: string; text: string }[];
  ai?: AiUsage;
  // Arsune's site check before any agent starts; "failed" means that no agent started.
  site_check?: { state: string; reason: string | null } | null;
};

export type Claim = {
  id: string;
  hunter_account: string;
  claim_type: string;
  urls: string[];
  status: string;
  checker_reason?: string | null;
  evidence_hash?: string | null;
  scout?: { account: string; share_bps: number; tip_id?: string } | null;
  created_at?: string;
  updated_at?: string;
};

export type LedgerRow = {
  id?: string;
  kind: string;
  amount: number;
  account: string;
  tx_id?: string | null;
  settlement_mode?: string | null;
  status?: string;
  explorer_url?: string | null;
  created_at?: string;
};

export type HunterStat = {
  account: string;
  claims: number;
  proven: number;
  rejected: number;
  duplicate: number;
  unverified: number;
  pending: number;
  earned: number;
  bonds_lost: number;
  scout_earned: number;
};

export type Bounty = {
  id: string;
  url: string;
  status: string;
  paused_reason?: string | null;
  reward_amount: number;
  bond_amount: number;
  fee_bps: number;
  budget_amount: number;
  budget_target?: number | null;
  settlement_mode: SettlementMode;
  created_at?: string;
  claims?: Claim[];
  ledger?: LedgerRow[];
  hunters?: HunterStat[];
};
