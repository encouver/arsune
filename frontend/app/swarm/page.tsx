"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { settlementBadge, siteName } from "../bounties/api";
import { SimTag } from "../bounties/ui";
import {
  claimGroup,
  claimKind,
  claimPage,
  claimReason,
  titler,
  GROUPS,
  isHexAccount,
} from "../claim-text";
import SiteHeader from "../site-header";
import { clock, pagePath, share, usdc } from "./format";
import RaceLine, { PoolBar } from "./race-line";
import {
  claimsOfAgent,
  claimsOfRace,
  finishedStory,
  ledgerOfClaims,
  lineCounts,
  poolOf,
  runningStory,
  withRecords,
} from "./race-model";
import styles from "./swarm.module.css";
import type {
  Agent,
  Bounty,
  Claim,
  HunterStat,
  LedgerRow,
  SwarmState,
} from "./types";
import WhoWon from "./who-won";

const POLL_MS = 1800;
const LEDGER_LABELS: Record<string, string> = {
  fund: "Budget funded",
  bond_in: "Deposit paid",
  reward_out: "Reward to hunter",
  bond_refund: "Deposit returned",
  bond_forfeit: "Deposit lost",
  fee: "Arsune fee",
  scout_share: "Scout share",
};
const CLAIM_FILTERS: [string, string][] = [
  ["all", "All"],
  ["paid", "Proven"],
  ["rejected", "Fake"],
  ["duplicate", "Repeats"],
];

const FILTER_GROUPS: Record<string, string> = {
  paid: "proven",
  rejected: "false",
  duplicate: "repeat",
};

function matchesFilter(c: Claim, f: string) {
  return f === "all" || claimGroup(c) === FILTER_GROUPS[f];
}

type Names = Map<string, string>;
// The claim list shows one agent's claims; kept by account, so it survives polls.
type AgentFilter = { account: string; role: Agent["role"]; name: string };

function nameOf(account: string, names: Names): string {
  return (
    names.get(account) ?? (isHexAccount(account) ? "an unnamed agent" : account)
  );
}

// The agent name (hunter-057); a 64-hex account shows only in the tooltip.
function Who({ account, names }: { account: string; names: Names }) {
  const name = names.get(account);
  const readable = !isHexAccount(account);
  return (
    <span className="sw-acct" title={account}>
      {name ? (
        <>
          <b>{name}</b>
          {readable && ` ${account}`}
        </>
      ) : readable ? (
        account
      ) : (
        "Unnamed agent"
      )}
    </span>
  );
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
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

function agentsFromHunters(hunters: HunterStat[], claims: Claim[]): Agent[] {
  // Scout accounts sit in the hunters list too; claims tell them apart.
  const hunting = new Set(claims.map((c) => c.hunter_account));
  const scouting = new Set(claims.map((c) => c.scout?.account));
  return hunters.map((h, i) => ({
    id: `hunter-${String(i + 1).padStart(2, "0")}`,
    account: h.account,
    role:
      scouting.has(h.account) && !hunting.has(h.account) ? "scout" : "hunter",
    strategy: "not reported",
    state: h.pending > 0 ? "waiting" : "searching",
    balance: 0,
    claims: h.claims,
    proven: h.proven,
    earned: h.earned + h.scout_earned,
    bonds_lost: h.bonds_lost,
  }));
}

function sum(rows: LedgerRow[], kind: string) {
  return rows
    .filter((r) => r.kind === kind && r.status === "completed")
    .reduce((a, r) => a + r.amount, 0);
}

export default function SwarmPage() {
  const [rawSwarm, setSwarm] = useState<SwarmState | null>(null);
  // The bounty named in ?bounty=; the race that runs now can be for another one.
  const [wanted, setWanted] = useState<string | null>(null);
  const [swarmDown, setSwarmDown] = useState(false);
  const [bounty, setBounty] = useState<Bounty | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [agentFilter, setAgentFilter] = useState<AgentFilter | null>(null);
  const [filter, setFilter] = useState("all");
  const swarmRef = useRef<SwarmState | null>(null);

  // A filter for an agent of one race does not fit the next bounty.
  useEffect(() => setAgentFilter(null), [bounty?.id]);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      const errs: string[] = [];
      const wanted = new URLSearchParams(window.location.search).get("bounty");
      if (!stop) setWanted(wanted);
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
            "Live hunt data is not answering. Showing the last good state.",
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

  const other = !!(
    wanted &&
    rawSwarm?.bounty_id &&
    rawSwarm.bounty_id !== wanted
  );
  const idle = !!rawSwarm && !rawSwarm.run_id;
  // Agents, tips and activity of another bounty's race do not belong on this board.
  const swarm = other || idle ? null : rawSwarm;
  const hunters = useMemo(() => deriveHunters(bounty), [bounty]);
  // Money comes from Arsune's records; the race host still tells each agent's state.
  const agents = useMemo(
    () =>
      swarm?.agents?.length
        ? withRecords(swarm.agents, hunters)
        : agentsFromHunters(hunters, bounty?.claims ?? []),
    [swarm, hunters, bounty],
  );
  const names: Names = useMemo(
    () => new Map((rawSwarm?.agents ?? []).map((a) => [a.account, a.id])),
    [rawSwarm],
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
  const siteFailed = swarm?.site_check?.state === "failed";
  const finished =
    !!swarm?.finished ||
    (agents.length > 0 &&
      agents.every((a) => a.state === "done" || a.state === "broke"));
  // With race state, the page shows this race only; without it, the whole bounty.
  const raceClaims = useMemo(
    () => (swarm ? claimsOfRace(claims, swarm.started_at) : claims),
    [swarm, claims],
  );
  const raceRows = swarm ? ledgerOfClaims(rows, raceClaims) : rows;
  const agentClaims = agentFilter
    ? claimsOfAgent(raceClaims, agentFilter.account, agentFilter.role)
    : raceClaims;
  const shownClaims = agentClaims.filter((c) => matchesFilter(c, filter));
  const titleOf = titler(claims);
  // Only a race of this bounty with agents has a story; without one the notices below speak.
  const reason = swarm?.site_check?.reason;
  const story =
    !swarm || !bounty || siteFailed
      ? null
      : finished && swarm.agents.length === 0
        ? `The hunt ended before any agent started.${reason ? ` ${reason}` : ""}`
        : finished
          ? finishedStory(lineCounts(raceClaims, raceRows))
          : swarm.agents.length
            ? runningStory(swarm.agents.length, siteName(bounty.url))
            : null;

  function pickAgent(a: Agent) {
    if (agentFilter?.account === a.account) return setAgentFilter(null);
    setAgentFilter({ account: a.account, role: a.role, name: a.id });
    document.getElementById("claims")?.scrollIntoView();
  }

  return (
    <main>
      <SiteHeader current="/swarm" />
      <div className="sw">
        <div className="rv-head">
          <h1 className="rv-site">
            {bounty ? siteName(bounty.url) : "Bug hunt"}
          </h1>
          <span className="sw-live" aria-live="polite">
            <i className={swarm && !finished ? "sw-pulse" : ""} />
            {other
              ? "No hunt for this site now"
              : idle
                ? "No hunt has run yet"
                : swarm
                  ? finished
                    ? "Hunt finished"
                    : `Hunt running · ${swarm.agents.length} agents`
                  : swarmDown
                    ? "Live data offline"
                    : "Connecting"}
          </span>
          <div className="sw-meta">
            <SimTag show={dry} />
            {bounty && !dry && (
              <span className="sw-real">
                {settlementBadge(bounty.settlement_mode)}
              </span>
            )}
          </div>
        </div>
        {story && (
          <p className="rv-story" data-testid="race-story" aria-live="polite">
            {story}
          </p>
        )}

        {other && (
          <div className="notice" role="status" data-testid="other-race-banner">
            Another hunt is running now. This board shows{" "}
            {bounty ? siteName(bounty.url) : "this hunt"} from Arsune&apos;s
            records only.{" "}
            <a
              href={`/swarm?bounty=${encodeURIComponent(rawSwarm?.bounty_id ?? "")}`}
            >
              Watch the live hunt
            </a>{" "}
            ·{" "}
            <a href={`/bounties/${encodeURIComponent(wanted ?? "")}`}>
              Start a hunt for this site
            </a>
          </div>
        )}
        {errors.length > 0 && (
          <div className="sw-error" role="status" data-testid="error-banner">
            {errors.join(" ")}
          </div>
        )}
        {swarm?.site_check?.state === "failed" && (
          <div
            className="sw-error"
            role="alert"
            data-testid="site-check-banner"
          >
            No agent started: {swarm.site_check.reason}
          </div>
        )}
        {bounty?.status === "paused" && (
          <div className="sw-error" role="alert" data-testid="paused-banner">
            The hunt is paused: Arsune cannot load this site, so agents wait
            until the site loads again. {bounty.paused_reason}
          </div>
        )}
        {loaded && !bounty && errors.length === 0 && (
          <div className="notice">
            No hunt yet. <a href="/">Start a hunt</a>, then come back here.
          </div>
        )}

        {bounty && (
          <section className="rv-card" aria-label="Hunt">
            <PoolBar pool={poolOf(bounty.budget_amount, rows)} />
            {swarm?.ai && swarm.ai.agents > 0 && (
              <p className="rv-ai" data-testid="ai-usage">
                <span className={styles.badge}>AI</span>
                <span>
                  <strong>{swarm.ai.agents}</strong> AI agents think with{" "}
                  <code>{swarm.ai.model}</code>
                </span>
                <span>
                  <strong>{swarm.ai.calls}</strong> model calls
                </span>
                <span>
                  <strong>{swarm.ai.usd_spent.toFixed(4)}</strong> of{" "}
                  {swarm.ai.budget_usd.toFixed(2)} USD AI budget spent
                </span>
              </p>
            )}
            <RaceLine
              claims={siteFailed ? [] : raceClaims}
              ledger={siteFailed ? [] : raceRows}
              agentCount={swarm ? swarm.agents.length : null}
              nameOf={(account) => nameOf(account, names)}
              titleOf={titleOf}
              finished={finished || !swarm}
              seeAllHref="#claims"
              resetKey={bounty.id}
            />
          </section>
        )}

        <WhoWon
          agents={agents}
          fallback={!swarm?.agents?.length}
          running={!!swarm && !finished}
          picked={agentFilter?.account ?? null}
          onPick={pickAgent}
        />

        <section className="sw-panel" aria-label="Reports" id="claims">
          <div className="sw-panel-top">
            <h2>Reports</h2>
            <span>{raceClaims.length} reports</span>
          </div>
          <div className="sw-filters" role="group" aria-label="Report filter">
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
                {label} (
                {agentClaims.filter((c) => matchesFilter(c, key)).length})
              </button>
            ))}
          </div>
          {agentFilter && (
            <p className="rv-agent-filter" data-testid="agent-filter">
              Reports by <b>{agentFilter.name}</b> ·{" "}
              <button type="button" onClick={() => setAgentFilter(null)}>
                Show all
              </button>
            </p>
          )}
          <ol className="sw-stream" data-testid="claim-stream">
            {shownClaims.length === 0 && (
              <li className="sw-empty">
                {raceClaims.length === 0
                  ? "No reports yet."
                  : "No reports match."}
              </li>
            )}
            {shownClaims.slice(0, 60).map((c) => (
              <ClaimRow key={c.id} claim={c} title={titleOf(c)} names={names} />
            ))}
          </ol>
        </section>

        <details className="rv-more" data-testid="more-details">
          <summary>More details</summary>
          <div className="sw-cols">
            <section className="sw-panel" aria-label="Payments">
              <div className="sw-panel-top">
                <h2>Payments</h2>
              </div>
              <dl className="rv-pay">
                {(
                  [
                    ["pay-fees", "Arsune fees", sum(rows, "fee")],
                    [
                      "pay-forfeited",
                      "Deposits lost",
                      sum(rows, "bond_forfeit"),
                    ],
                    ["pay-scouts", "Paid to scouts", sum(rows, "scout_share")],
                    ["pay-false-cost", "Cost of fake reports to you", 0],
                  ] as const
                ).map(([id, label, amount]) => (
                  <div key={id} data-testid={id}>
                    <dt>{label}</dt>
                    <dd>{usdc(amount)} USDC</dd>
                  </div>
                ))}
              </dl>
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
                      {rowDry ? null : r.explorer_url ? (
                        <a
                          href={r.explorer_url}
                          target="_blank"
                          rel="noreferrer"
                        >
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

            <section className="sw-panel" aria-label="Scout tips">
              <div className="sw-panel-top">
                <h2>Scout tips</h2>
                <span>{swarm?.tips.length ?? 0} tips</span>
              </div>
              <ul className="sw-tips" data-testid="tips">
                {!swarm?.tips.length && (
                  <li className="sw-empty">No scout tips yet.</li>
                )}
                {swarm?.tips.map((t) => (
                  <li key={t.id} data-status={t.status}>
                    <div>
                      <strong>{t.scout}</strong> offers a tip:{" "}
                      <em>{claimKind(t.claim_type)}</em>
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
                    <time>{clock(l.at)}</time> <strong>{l.agent}</strong>{" "}
                    {l.text}
                  </li>
                ))}
            </ol>
          </section>
        </details>
      </div>
    </main>
  );
}

function ClaimRow({
  claim: c,
  title,
  names,
}: {
  claim: Claim;
  title: string;
  names: Names;
}) {
  const group = claimGroup(c);
  const reason = claimReason(c);
  return (
    <li
      className={`sw-claim${group === "proven" ? " sw-claim-good" : ""}${group === "false" ? " sw-claim-bad" : ""}`}
      data-testid="claim-row"
      data-status={c.status}
    >
      <div className="sw-claim-top">
        <strong className="sw-claim-title" data-testid="claim-title">
          {title}
        </strong>
        <span className={`sw-chip sw-s-${c.status}`}>
          {c.status === "unfunded" ? "Proven, not paid" : GROUPS[group].chip}
        </span>
      </div>
      <div className="sw-muted sw-claim-meta">
        <code>{claimPage(c)}</code> · by{" "}
        <Who account={c.hunter_account} names={names} />
        {c.scout &&
          ` · tip from ${nameOf(c.scout.account, names)} (${share(c.scout.share_bps)})`}
        {c.evidence_url && (
          <>
            {" · "}
            <a
              href={c.evidence_url}
              target="_blank"
              rel="noreferrer"
              title={c.evidence_hash ?? undefined}
              data-testid="claim-evidence"
            >
              Evidence
            </a>
          </>
        )}
      </div>
      {reason && <p className="sw-reason">{reason}</p>}
    </li>
  );
}
