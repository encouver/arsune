"use client";

import { useEffect, useState } from "react";
import {
  api,
  bountyLabel,
  BountySummary,
  errorText,
  OwnerBounty,
  siteName,
  usdc,
  utcTime,
} from "../bounties/api";
import { Message, SimTag } from "../bounties/ui";
import { countGroups, countText } from "../claim-text";
import { getHost, type Host } from "../race-host";
import SiteHeader from "../site-header";

// Claim counts need each bounty's detail; the newest races get them.
const DETAILED = 20;

export default function RacesPage() {
  const [items, setItems] = useState<BountySummary[] | null>(null);
  const [dry, setDry] = useState(false);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const [host, setHost] = useState<Host | null>(null);
  const [error, setError] = useState<string | null>(null);

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
              ? `${d.claims.length} claims · ${countText(countGroups(d.claims))}`
              : "No claims yet";
        setCounts(next);
      })
      .catch((e) => !stop && setError(errorText(e)));
    return () => {
      stop = true;
    };
  }, []);

  const racing = host?.busy ? host.bounty_id : null;

  return (
    <>
      <SiteHeader current="/races" />
      <main>
        <div className="sw">
          <div className="sw-head">
            <div>
              <p className="eyebrow">Races</p>
              <h1>Your bug races</h1>
              <p className="sw-sub">
                Open a race to see its proven bugs, or to fund it and start it
                again.
              </p>
            </div>
            <div className="sw-meta">
              <SimTag show={dry} />
              <a className="bo-board-link" href="/">
                Start a new race →
              </a>
            </div>
          </div>
          <Message kind="error" text={error} testId="races-error" />
          {items && items.length === 0 && (
            <p className="bo-text" data-testid="races-empty">
              No races yet. <a href="/">Start the first one.</a>
            </p>
          )}
          {items === null && !error && (
            <p className="bo-muted">Loading the races…</p>
          )}
          <ul className="ra-list" data-testid="races">
            {items?.map((b) => (
              <li key={b.id}>
                <a
                  className="ra-row"
                  href={`/bounties/${b.id}`}
                  data-testid="race-row"
                >
                  <span className="ra-site">{siteName(b.url)}</span>
                  <span className="ra-status">
                    {racing === b.id && (
                      <span className="ra-live" data-testid="racing-now">
                        Racing now
                      </span>
                    )}
                    <span
                      className={`sw-chip ${b.status === "open" ? "bo-chip-done" : "bo-chip-active"}`}
                    >
                      {bountyLabel(b)}
                    </span>
                  </span>
                  <span className="ra-counts" data-testid="race-counts">
                    {counts[b.id] ?? ""}
                  </span>
                  <span className="ra-meta">
                    {usdc(b.budget_amount)} USDC left · created{" "}
                    {utcTime(b.created_at)}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      </main>
    </>
  );
}
