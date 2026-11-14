"use client";

import { useEffect, useState } from "react";
import {
  api,
  bountyLabel,
  BountySummary,
  errorText,
  localOwnedIds,
  OwnerBounty,
  siteName,
  usdc,
  utcTime,
} from "../bounties/api";
import { Message, SimTag } from "../bounties/ui";
import { countGroups, countText } from "../claim-text";
import { getHost, type Host } from "../race-host";
import { forgetSessionOn, sessionHeaders } from "../owner-session";
import { useOwnerSession } from "../owner-sign-in";
import SiteHeader from "../site-header";

// Claim counts need each bounty's detail; the newest races get them.
const DETAILED = 20;
// Hunts of this browser that are not in the newest list are read one by one, up to this many.
const LOCAL_LOOKUPS = 30;

/** The signed-in account's hunts and this browser's hunts, newest first. */
async function myHunts(
  account: string | null,
  known: BountySummary[],
): Promise<BountySummary[]> {
  const byId = new Map<string, BountySummary>();
  if (account) {
    try {
      const own = await api<{ items: BountySummary[] }>(
        "/api/owner/bounties",
        undefined,
        { headers: sessionHeaders() },
      );
      for (const b of own.items) byId.set(b.id, b);
    } catch (e) {
      forgetSessionOn(e);
    }
  }
  const listed = new Map(known.map((b) => [b.id, b]));
  const missing: string[] = [];
  for (const id of localOwnedIds()) {
    if (byId.has(id)) continue;
    const b = listed.get(id);
    if (b) byId.set(id, b);
    else missing.push(id);
  }
  const found = await Promise.all(
    missing
      .slice(0, LOCAL_LOOKUPS)
      .map((id) =>
        api<OwnerBounty>(`/api/bounties/${encodeURIComponent(id)}`).catch(
          () => null,
        ),
      ),
  );
  for (const b of found) if (b) byId.set(b.id, b);
  return [...byId.values()].sort((a, b) =>
    b.created_at.localeCompare(a.created_at),
  );
}

export default function RacesPage() {
  const [items, setItems] = useState<BountySummary[] | null>(null);
  const [dry, setDry] = useState(false);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [host, setHost] = useState<Host | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mine, setMine] = useState<BountySummary[]>([]);
  const session = useOwnerSession();
  const account = session?.account_id ?? null;

  useEffect(() => {
    if (items === null) return;
    let stop = false;
    myHunts(account, items).then((m) => !stop && setMine(m));
    return () => {
      stop = true;
    };
  }, [account, items]);

  useEffect(() => {
    let stop = false;
    getHost().then((h) => !stop && setHost(h));
    api<{ items: BountySummary[]; settlement_mode: string }>("/api/bounties")
      .then(async (list) => {
        if (stop) return;
        setItems(list.items);
        setDry(list.settlement_mode === "dry");
        const details = await Promise.all(
          list.items
            .slice(0, DETAILED)
            .map((b) =>
              api<OwnerBounty>(`/api/bounties/${b.id}`).catch(() => null),
            ),
        );
        if (stop) return;
        const next: Record<string, string> = {};
        for (const d of details)
          if (d)
            next[d.id] = d.claims.length
              ? `${d.claims.length} report${d.claims.length === 1 ? "" : "s"} · ${countText(countGroups(d.claims))}`
              : "No reports yet";
        setCounts(next);
      })
      .catch((e) => !stop && setError(errorText(e)));
    return () => {
      stop = true;
    };
  }, []);

  const racing = host?.busy ? host.bounty_id : null;

  function row(b: BountySummary, mine = false) {
    const id = (name: string) => (mine ? `my-${name}` : name);
    return (
      <li key={b.id}>
        <a
          className="ra-row"
          href={`/bounties/${b.id}`}
          data-testid={id("race-row")}
        >
          <span className="ra-site">{siteName(b.url)}</span>
          <span className="ra-status">
            {racing === b.id && (
              <span className="ra-live" data-testid={id("racing-now")}>
                Hunting now
              </span>
            )}
            <span
              className={`sw-chip ${b.status === "open" ? "bo-chip-done" : "bo-chip-active"}`}
            >
              {bountyLabel(b)}
            </span>
          </span>
          <span className="ra-counts" data-testid={id("race-counts")}>
            {counts[b.id] ?? ""}
          </span>
          <span className="ra-meta">
            {usdc(b.budget_amount)} USDC left · created {utcTime(b.created_at)}
          </span>
        </a>
      </li>
    );
  }

  return (
    <>
      <SiteHeader current="/races" />
      <main>
        <div className="sw">
          <div className="sw-head">
            <div>
              <p className="eyebrow">Hunts</p>
              <h1>Your bug hunts</h1>
              <p className="sw-sub">
                Open a hunt to see its proven bugs, or to fund it and run it
                again.
              </p>
            </div>
            <div className="sw-meta">
              <SimTag show={dry} />
              <a className="bo-board-link" href="/">
                Start a new hunt →
              </a>
            </div>
          </div>
          {(account || mine.length > 0) && (
            <section className="ra-mine" data-testid="my-races">
              <h2>Your hunts</h2>
              {mine.length === 0 ? (
                <p className="bo-muted">No hunts for {account} yet.</p>
              ) : (
                <ul className="ra-list">{mine.map((b) => row(b, true))}</ul>
              )}
              <h2>All hunts</h2>
            </section>
          )}
          <Message kind="error" text={error} testId="races-error" />
          {items && items.length === 0 && (
            <p className="bo-text" data-testid="races-empty">
              No hunts yet. <a href="/">Start the first one.</a>
            </p>
          )}
          {items === null && !error && (
            <p className="bo-muted">Loading your hunts…</p>
          )}
          <ul className="ra-list" data-testid="races">
            {items?.map((b) => row(b))}
          </ul>
        </div>
      </main>
    </>
  );
}
