"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import SiteHeader from "../site-header";
import { clock, pagePath, share, usdc } from "./format";
import type {
  Agent,
  Bounty,
  Claim,
  HunterStat,
  LedgerRow,
  SwarmState,
} from "./types";

const POLL_MS = 1800;
const FLASH_MS = 6000;
const LEDGER_LABELS: Record<string, string> = {
  fund: "Bounty funded",
  bond_in: "Bond posted",
  reward_out: "Reward to hunter",
  bond_refund: "Bond refunded",
  bond_forfeit: "Bond forfeited",
  fee: "Arsune fee",
  scout_share: "Scout share",
};
const STATUS_LABELS: Record<string, string> = {
  unfunded: "proven, unfunded",
  needs_review: "needs review",
};
const GLYPHS: Record<string, string> = {
  searching: "◌",
  negotiating: "⇄",
  claiming: "▲",
  waiting: "…",
  broke: "–",
  done: "✓",
  paid: "$",
  lost: "✕",
};

const RESULT_GLYPHS: Record<string, string> = {
  earned: "$",
  lost: "✕",
  broke: "✕",
  none: "·",
};
const RESULT_LABELS: Record<string, string> = {
  earned: "Earned",
  lost: "Lost money",
  broke: "Went broke",
  none: "Found nothing",
};
const CLAIM_FILTERS: [string, string][] = [
  ["all", "All"],
  ["paid", "Paid"],
  ["rejected", "Rejected"],
  ["duplicate", "Duplicate"],
];

type Result = "earned" | "lost" | "broke" | "none";

function resultOf(a: Agent): Result {
  if (a.state === "broke") return "broke";
  if (a.bonds_lost > a.earned) return "lost";
  if (a.earned > 0) return "earned";
  return "none";
}

function matchesFilter(c: Claim, f: string) {
  if (f === "paid") return c.status === "proven" || c.status === "paid";
  return f === "all" || c.status === f;
}

type Flash = Record<string, { kind: "paid" | "lost"; until: number }>;
type Names = Map<string, string>;

// Agent name first (hunter-057), then the account, so 64-hex accounts stay readable.
function Who({ account, names }: { account: string; names: Names }) {
  const name = names.get(account);
  return (
    <span className="sw-acct" title={account}>
      {name ? (
        <>
          <b>{name}</b> {account}
        </>
      ) : (
        account
      )}
    </span>
  );
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}

function sum(rows: LedgerRow[], kind: string) {
  return rows.filter((r) => r.kind === kind).reduce((a, r) => a + r.amount, 0);
}

function deriveHunters(b: Bounty | null): HunterStat[] {
  if (!b) return [];
  if (b.hunters) return b.hunters;
  const map = new Map<string, HunterStat>();
  for (const c of b.claims ?? []) {
    const h = map.get(c.hunter_account) ?? {
      account: c.hunter_account,
      claims: 0,
      proven: 0,
      rejected: 0,
      duplicate: 0,
      unverified: 0,
      pending: 0,
      earned: 0,
      bonds_lost: 0,
      scout_earned: 0,
    };
    h.claims++;
    if (c.status === "proven" || c.status === "paid") h.proven++;
    else if (c.status === "rejected") h.rejected++;
    else if (c.status === "duplicate") h.duplicate++;
    else if (c.status === "unverified") h.unverified++;
    else h.pending++;
    map.set(c.hunter_account, h);
  }
  for (const r of b.ledger ?? []) {
    const h = map.get(r.account);
    if (!h) continue;
    if (r.kind === "reward_out") h.earned += r.amount;
    if (r.kind === "bond_forfeit") h.bonds_lost += r.amount;
    if (r.kind === "scout_share") h.scout_earned += r.amount;
  }
  return [...map.values()];
}

function agentsFromHunters(hunters: HunterStat[]): Agent[] {
  return hunters.map((h, i) => ({
    id: `hunter-${String(i + 1).padStart(2, "0")}`,
    account: h.account,
    role: "hunter",
    strategy: "not reported",
    state: h.pending > 0 ? "waiting" : "searching",
    balance: 0,
    claims: h.claims,
    proven: h.proven,
    earned: h.earned,
    bonds_lost: h.bonds_lost,
  }));
}

function SimTag({ dry }: { dry: boolean }) {
  return dry ? (
    <span className="sw-sim" data-testid="sim-label">
      Simulation — no real money
    </span>
  ) : null;
}

function useFlashes(agents: Agent[]): Flash {
  const prev = useRef<Map<string, Agent>>(new Map());
  const [flash, setFlash] = useState<Flash>({});
  useEffect(() => {
    const now = Date.now();
    const next: Flash = {};
    for (const a of agents) {
      const old = prev.current.get(a.account);
      if (old && a.earned > old.earned)
        next[a.account] = { kind: "paid", until: now + FLASH_MS };
      else if (old && a.bonds_lost > old.bonds_lost)
        next[a.account] = { kind: "lost", until: now + FLASH_MS };
    }
    prev.current = new Map(agents.map((a) => [a.account, a]));
    if (Object.keys(next).length) setFlash((f) => ({ ...f, ...next }));
  }, [agents]);
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setFlash((f) => {
        const keep = Object.entries(f).filter(([, v]) => v.until > now);
        return keep.length === Object.keys(f).length
          ? f
          : Object.fromEntries(keep);
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);
  return flash;
}

export default function SwarmPage() {
  const [swarm, setSwarm] = useState<SwarmState | null>(null);
  const [swarmDown, setSwarmDown] = useState(false);
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [picked, setPicked] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const swarmRef = useRef<SwarmState | null>(null);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      const errs: string[] = [];
      const wanted = new URLSearchParams(window.location.search).get("bounty");
      try {
        const s = await getJson<SwarmState>("/swarm-state");
        swarmRef.current = s;
        if (!stop) {
          setSwarm(s);
          setSwarmDown(false);
        }
      } catch {
        if (!stop) setSwarmDown(true);
        if (swarmRef.current)
          errs.push(
            "Swarm runner is not answering. Showing the last good state.",
          );
      }
      try {
        let id = wanted || swarmRef.current?.bounty_id || null;
        if (!id) {
          const list = await getJson<{ items?: Bounty[] } | Bounty[]>(
            "/api/bounties",
          );
          const items = Array.isArray(list) ? list : (list.items ?? []);
          const newest = [...items].sort((a, b) =>
            (b.created_at ?? "").localeCompare(a.created_at ?? ""),
          )[0];
          id = newest?.id ?? null;
        }
        if (id) {
          const b = await getJson<Bounty>(
            `/api/bounties/${encodeURIComponent(id)}`,
          );
          if (!stop) setBounty(b);
        }
      } catch {
        errs.push("Arsune API is not answering. Showing the last good state.");
      }
      if (!stop) {
        setErrors(errs);
        setLoaded(true);
        timer = setTimeout(tick, POLL_MS);
      }
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);

  const hunters = useMemo(() => deriveHunters(bounty), [bounty]);
  const agents = useMemo(
    () => (swarm?.agents?.length ? swarm.agents : agentsFromHunters(hunters)),
    [swarm, hunters],
  );
  const flash = useFlashes(agents);
  const names: Names = useMemo(
    () => new Map((swarm?.agents ?? []).map((a) => [a.account, a.id])),
    [swarm],
  );
  const claims = useMemo(
    () =>
      [...(bounty?.claims ?? [])].sort((a, b) =>
        (b.created_at ?? "").localeCompare(a.created_at ?? ""),
      ),
    [bounty],
  );
  const ledger = useMemo(() => [...(bounty?.ledger ?? [])].reverse(), [bounty]);
  const dry = bounty?.settlement_mode === "dry";
  const rows = bounty?.ledger ?? [];
  const totals = {
    funded: sum(rows, "fund"),
    hunters: sum(rows, "reward_out"),
    scouts: sum(rows, "scout_share"),
    fees: sum(rows, "fee"),
    forfeited: sum(rows, "bond_forfeit"),
    proven: (bounty?.claims ?? []).filter(
      (c) => c.status === "proven" || c.status === "paid",
    ).length,
    rejected: (bounty?.claims ?? []).filter((c) => c.status === "rejected")
      .length,
  };
  const byAccount = useMemo(
    () => new Map((swarm?.agents ?? []).map((a) => [a.account, a])),
    [swarm],
  );
  // The API has no proven count for scouts, so the swarm state fills it in.
  const board = useMemo(() => {
    const rows = hunters.map((h) => {
      const a = byAccount.get(h.account);
      if (a?.role !== "scout") return h;
      return {
        ...h,
        proven: a.proven,
        earned: Math.max(h.earned + h.scout_earned, a.earned),
        scout_earned: 0,
      };
    });
    const seen = new Set(rows.map((h) => h.account));
    for (const a of swarm?.agents ?? []) {
      if (a.role === "scout" && !seen.has(a.account) && a.earned > 0)
        rows.push({
          account: a.account,
          claims: a.claims,
          proven: a.proven,
          rejected: 0,
          duplicate: 0,
          unverified: 0,
          pending: 0,
          earned: a.earned,
          bonds_lost: a.bonds_lost,
          scout_earned: 0,
        });
    }
    return rows;
  }, [hunters, byAccount, swarm]);
  const leaderboard = useMemo(
    () =>
      [...board]
        .sort((a, b) => b.earned + b.scout_earned - (a.earned + a.scout_earned))
        .slice(0, 10),
    [board],
  );
  const losers = useMemo(() => {
    const lost = new Map<string, number>();
    for (const h of hunters) lost.set(h.account, h.bonds_lost);
    for (const a of agents)
      lost.set(a.account, Math.max(lost.get(a.account) ?? 0, a.bonds_lost));
    return [...lost.entries()]
      .filter(([, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 5);
  }, [hunters, agents]);
  const finished =
    !!swarm?.finished ||
    (agents.length > 0 &&
      agents.every((a) => a.state === "done" || a.state === "broke"));
  const counts = { earned: 0, lost: 0, broke: 0, none: 0 };
  for (const a of agents) counts[resultOf(a)]++;
  const shownClaims = claims.filter((c) => matchesFilter(c, filter));
  const selected = agents.find((a) => a.id === picked) ?? null;

  return (
    <main>
      <SiteHeader current="/swarm" />
      <div className="sw">
        <div className="sw-head">
          <div>
            <p className="eyebrow">Live swarm board</p>
            <h1>
              {agents.length || "No"} agents racing to break{" "}
              <span className="sw-site">
                {bounty ? bounty.url.replace(/^https?:\/\//, "") : "a site"}
              </span>
            </h1>
            <p className="sw-sub">
              Only bugs that Arsune proves in a fresh browser get paid. False
              claims lose their bond.
            </p>
          </div>
          <div className="sw-meta">
            <SimTag dry={!!dry} />
            {bounty && !dry && (
              <span className="sw-real">Real settlement via NEAR Intents</span>
            )}
            <span className="sw-live" aria-live="polite">
              <i className={swarm && !swarm.finished ? "sw-pulse" : ""} />
              {swarm
                ? swarm.finished
                  ? "Run finished"
                  : "Live"
                : swarmDown
                  ? "Swarm runner offline"
                  : "Connecting"}
            </span>
          </div>
        </div>

        {errors.length > 0 && (
          <div className="sw-error" role="status" data-testid="error-banner">
            {errors.join(" ")}
          </div>
        )}
        {loaded && !bounty && errors.length === 0 && (
          <div className="notice">
            No bounty found yet. Create one through the API, then reload.
          </div>
        )}

        <section className="sw-totals" aria-label="Totals">
          {[
            ["Pool funded", usdc(totals.funded), true],
            ["Paid to hunters", usdc(totals.hunters), true],
            ["Paid to scouts", usdc(totals.scouts), true],
            ["Arsune fees", usdc(totals.fees), true],
            ["Bonds forfeited", usdc(totals.forfeited), true],
            ["Proven bugs", String(totals.proven), false],
            ["False claims rejected", String(totals.rejected), false],
            ["Cost of false claims to the owner", "0", true],
          ].map(([label, value, money]) => (
            <div
              className="sw-stat"
              key={label as string}
              data-testid={`total-${(label as string).toLowerCase().replace(/[^a-z]+/g, "-")}`}
            >
              <span className="sw-stat-label">{label}</span>
              <strong>
                {value}
                {money && <small> USDC</small>}
              </strong>
            </div>
          ))}
          {dry && (
            <div className="sw-totals-note">
              <SimTag dry /> These amounts are simulated.
            </div>
          )}
        </section>

        <div className="sw-cols">
          <section className="sw-panel sw-grid-panel" aria-label="Agent grid">
            <div className="sw-panel-top">
              <h2>Agents</h2>
              <Legend />
            </div>
            {finished && agents.length > 0 && (
              <div className="sw-results" data-testid="race-results">
                <ul className="sw-legend" aria-label="Result colors">
                  {(["earned", "lost", "broke", "none"] as Result[]).map(
                    (r) => (
                      <li key={r}>
                        <i data-result={r} />
                        {RESULT_LABELS[r]}
                      </li>
                    ),
                  )}
                </ul>
                <p data-testid="result-counts">
                  {counts.earned} agents earned · {counts.lost} lost money ·{" "}
                  {counts.broke} went broke · {counts.none} found nothing
                </p>
              </div>
            )}
            {swarm == null && agents.length > 0 && (
              <p className="sw-hint">
                Swarm runner not connected. Tiles come from Arsune hunter
                records.
              </p>
            )}
            <div className="sw-tiles" data-testid="agent-grid">
              {agents.map((a) => {
                const f = flash[a.account];
                const st = f?.kind ?? a.state;
                const res = resultOf(a);
                const mark =
                  a.bonds_lost > 0
                    ? `−${usdc(a.bonds_lost)}`
                    : a.earned > 0
                      ? `+${usdc(a.earned)}`
                      : null;
                return (
                  <button
                    key={a.id}
                    type="button"
                    className="sw-tile"
                    data-testid="agent-tile"
                    data-state={st}
                    data-outcome={res}
                    data-result={finished ? res : undefined}
                    data-lost={a.bonds_lost > 0 ? "true" : undefined}
                    data-role={a.role}
                    aria-pressed={picked === a.id}
                    aria-label={`${a.id}, ${a.role}, ${st}${finished ? `, ${RESULT_LABELS[res]}` : ""}${a.bonds_lost > 0 ? `, lost ${usdc(a.bonds_lost)} in bonds` : ""}`}
                    title={`${a.id} (${a.role}) · ${st}\n${a.strategy}\nbalance ${usdc(a.balance)} · proven ${a.proven} · earned ${usdc(a.earned)} · bonds lost ${usdc(a.bonds_lost)}`}
                    onClick={() => setPicked(picked === a.id ? null : a.id)}
                  >
                    <span aria-hidden="true">
                      {finished ? RESULT_GLYPHS[res] : GLYPHS[st]}
                    </span>
                    {mark && (
                      <small
                        className="sw-mark"
                        data-testid="tile-mark"
                        data-kind={a.bonds_lost > 0 ? "lost" : "earned"}
                        aria-hidden="true"
                      >
                        {mark}
                      </small>
                    )}
                    <em aria-hidden="true">{a.id.replace(/^\D+/, "")}</em>
                    {a.role === "scout" && <b aria-hidden="true">S</b>}
                  </button>
                );
              })}
            </div>
            {selected && (
              <dl className="sw-detail" data-testid="agent-detail">
                <div>
                  <dt>Agent</dt>
                  <dd>
                    {selected.id} · {selected.role}
                  </dd>
                </div>
                <div>
                  <dt>Account</dt>
                  <dd>{selected.account}</dd>
                </div>
                <div>
                  <dt>Strategy</dt>
                  <dd>{selected.strategy}</dd>
                </div>
                <div>
                  <dt>State</dt>
                  <dd>{selected.state}</dd>
                </div>
                <div>
                  <dt>Balance</dt>
                  <dd>{usdc(selected.balance)} USDC</dd>
                </div>
                <div>
                  <dt>Proven / claims</dt>
                  <dd>
                    {selected.proven} / {selected.claims}
                  </dd>
                </div>
                <div>
                  <dt>Earned</dt>
                  <dd>{usdc(selected.earned)} USDC</dd>
                </div>
                <div>
                  <dt>Bonds lost</dt>
                  <dd>{usdc(selected.bonds_lost)} USDC</dd>
                </div>
                {dry && (
                  <div>
                    <SimTag dry />
                  </div>
                )}
              </dl>
            )}
          </section>

          <section className="sw-panel" aria-label="Claim stream">
            <div className="sw-panel-top">
              <h2>Claim stream</h2>
              <span>{claims.length} claims</span>
            </div>
            <div className="sw-filters" role="group" aria-label="Claim filter">
              {CLAIM_FILTERS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className="sw-filter"
                  data-testid={`filter-${key}`}
                  data-filter={key}
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                >
                  {label} ({claims.filter((c) => matchesFilter(c, key)).length})
                </button>
              ))}
            </div>
            <ol className="sw-stream" data-testid="claim-stream">
              {shownClaims.length === 0 && (
                <li className="sw-empty">
                  {claims.length === 0 ? "No claims yet." : "No claims match."}
                </li>
              )}
              {shownClaims.slice(0, 60).map((c) => (
                <ClaimRow key={c.id} claim={c} names={names} />
              ))}
            </ol>
          </section>
        </div>

        <div className="sw-cols sw-cols-3">
          <section className="sw-panel" aria-label="Payments">
            <div className="sw-panel-top">
              <h2>Payments</h2>
              <SimTag dry={!!dry} />
            </div>
            <ul className="sw-ledger" data-testid="ledger">
              {ledger.length === 0 && (
                <li className="sw-empty">No payments yet.</li>
              )}
              {ledger.slice(0, 60).map((r, i) => {
                const rowDry = dry || r.settlement_mode === "dry";
                return (
                  <li key={r.id ?? i} data-kind={r.kind}>
                    <span className="sw-kind">
                      {LEDGER_LABELS[r.kind] ?? r.kind}
                    </span>
                    <Who account={r.account} names={names} />
                    <strong>{usdc(r.amount)} USDC</strong>
                    {rowDry ? (
                      <span className="sw-sim sw-sim-small">Simulated</span>
                    ) : r.explorer_url ? (
                      <a href={r.explorer_url} target="_blank" rel="noreferrer">
                        NearBlocks
                      </a>
                    ) : (
                      <span className="sw-muted">pending</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>

          <section className="sw-panel" aria-label="Tip market">
            <div className="sw-panel-top">
              <h2>Tip market</h2>
              <span>{swarm?.tips.length ?? 0} tips</span>
            </div>
            <ul className="sw-tips" data-testid="tips">
              {!swarm?.tips.length && (
                <li className="sw-empty">No scout tips yet.</li>
              )}
              {swarm?.tips.map((t) => (
                <li key={t.id} data-status={t.status}>
                  <div>
                    <strong>{t.scout}</strong> offers a <em>{t.claim_type}</em>{" "}
                    tip
                    <span className="sw-muted"> {pagePath(t.urls)}</span>
                  </div>
                  <div className="sw-tip-flow">
                    <span>asks {share(t.ask_share_bps)}</span>
                    <span aria-hidden="true">→</span>
                    <span className={`sw-chip sw-tip-${t.status}`}>
                      {t.status}
                    </span>
                    {t.buyer && <span>to {t.buyer}</span>}
                    {t.final_share_bps != null && (
                      <span>final share {share(t.final_share_bps)}</span>
                    )}
                  </div>
                  {t.note && <p className="sw-muted">{t.note}</p>}
                </li>
              ))}
            </ul>
          </section>

          <section className="sw-panel" aria-label="Leaderboard">
            <div className="sw-panel-top">
              <h2>Leaderboard</h2>
              <SimTag dry={!!dry} />
            </div>
            <ol className="sw-board" data-testid="leaderboard">
              {leaderboard.length === 0 && (
                <li className="sw-empty">No hunters yet.</li>
              )}
              {leaderboard.map((h, i) => (
                <li key={h.account}>
                  <span className="sw-rank">{i + 1}</span>
                  <Who account={h.account} names={names} />
                  <span className="sw-muted">{h.proven} proven</span>
                  <strong>{usdc(h.earned + h.scout_earned)} USDC</strong>
                </li>
              ))}
            </ol>
            <h3 className="sw-sub-h">Biggest losers</h3>
            <ol className="sw-board sw-losers" data-testid="biggest-losers">
              {losers.length === 0 && (
                <li className="sw-empty">No bonds lost yet.</li>
              )}
              {losers.map(([account, n], i) => (
                <li key={account}>
                  <span className="sw-rank sw-rank-bad">{i + 1}</span>
                  <Who account={account} names={names} />
                  <span className="sw-muted">bonds lost</span>
                  <strong className="sw-loss-amt">−{usdc(n)} USDC</strong>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <section className="sw-panel" aria-label="Activity log">
          <div className="sw-panel-top">
            <h2>Activity</h2>
          </div>
          <ol className="sw-log" data-testid="activity-log">
            {!swarm?.log.length && (
              <li className="sw-empty">No activity yet.</li>
            )}
            {swarm?.log
              .slice(-40)
              .reverse()
              .map((l, i) => (
                <li key={`${l.at}-${i}`}>
                  <time>{clock(l.at)}</time> <strong>{l.agent}</strong> {l.text}
                </li>
              ))}
          </ol>
        </section>
      </div>
    </main>
  );
}

function ClaimRow({ claim: c, names }: { claim: Claim; names: Names }) {
  const good = c.status === "proven" || c.status === "paid";
  return (
    <li
      className={`sw-claim${good ? " sw-claim-good" : ""}${c.status === "rejected" ? " sw-claim-bad" : ""}`}
      data-testid="claim-row"
      data-status={c.status}
    >
      <div className="sw-claim-top">
        <Who account={c.hunter_account} names={names} />
        <span className={`sw-chip sw-s-${c.status}`}>
          {STATUS_LABELS[c.status] ?? c.status.replace("_", " ")}
        </span>
      </div>
      <div className="sw-muted">
        {c.claim_type} · {pagePath(c.urls)}
        {c.scout &&
          ` · tip from ${names.get(c.scout.account) ?? c.scout.account} (${share(c.scout.share_bps)})`}
      </div>
      {c.checker_reason && <p className="sw-reason">{c.checker_reason}</p>}
    </li>
  );
}

function Legend() {
  const items: [string, string][] = [
    ["searching", "Searching"],
    ["negotiating", "Negotiating"],
    ["claiming", "Claiming"],
    ["waiting", "Waiting"],
    ["paid", "Paid"],
    ["lost", "Lost bond"],
    ["broke", "Broke"],
  ];
  return (
    <ul className="sw-legend" aria-label="Tile colors">
      {items.map(([s, l]) => (
        <li key={s}>
          <i data-state={s} />
          {l}
        </li>
      ))}
    </ul>
  );
}
