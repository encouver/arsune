"use client";

import type { JSX, ReactNode } from "react";
import { usdc } from "./format";
import {
  latestSentence,
  lineCounts,
  rareText,
  signedUsdc,
  stageOf,
  type LineClaim,
  type LineLedgerRow,
  type NameOf,
  type Pool,
} from "./race-model";

export type RaceLineProps = {
  claims: LineClaim[];
  ledger: LineLedgerRow[];
  // null: the race state is not known, so Find shows no count.
  agentCount: number | null;
  nameOf: NameOf;
  titleOf: (c: LineClaim) => string;
  // true: no dots move.
  finished: boolean;
  seeAllHref: string;
};

export function PoolBar({ pool }: { pool: Pool }): JSX.Element | null {
  if (pool.fraction === null) return null;
  return (
    <div className="rv-pool" data-testid="pool-bar">
      <span className="rv-pool-name">Bounty pool</span>
      <span className="rv-pool-bar" aria-hidden="true">
        <i style={{ width: `${pool.fraction * 100}%` }} />
      </span>
      <span className="rv-pool-text">
        <b>{usdc(pool.left)}</b> of {usdc(pool.into)} USDC left
      </span>
    </div>
  );
}

function Step({
  n,
  name,
  children,
}: {
  n: number;
  name: string;
  children: ReactNode;
}) {
  return (
    <div className={`rv-step rv-step-${n}`} data-step={name.toLowerCase()}>
      <span className="rv-node">{n}</span>
      <span className="rv-label">
        <b>{name}</b>
        {children}
      </span>
    </div>
  );
}

function ResultRow({
  kind,
  name,
  count,
  note,
  testId,
  noteTestId,
}: {
  kind: string;
  name: string;
  count: number;
  note: string;
  testId: string;
  noteTestId?: string;
}) {
  return (
    <li className="rv-result" data-result={kind}>
      <i className="rv-swatch" />
      <span className="rv-result-name">{name}</span>
      <b className="rv-count" data-testid={testId}>
        {count}
      </b>
      <span className="rv-note" data-testid={noteTestId}>
        {note}
      </span>
    </li>
  );
}

// The part after the arrow is the result; it gets the result's weight and color.
function Latest({
  claims,
  text,
  claimId,
}: {
  claims: LineClaim[];
  text: string;
  claimId: string;
}) {
  const cut = text.indexOf(" → ");
  if (cut < 0) return <>{text}</>;
  const c = claims.find((x) => x.id === claimId);
  return (
    <>
      {text.slice(0, cut + 3)}
      <b className="rv-latest-result" data-stage={c ? stageOf(c) : "other"}>
        {text.slice(cut + 3)}
      </b>
    </>
  );
}

export default function RaceLine({
  claims,
  ledger,
  agentCount,
  nameOf,
  titleOf,
  seeAllHref,
}: RaceLineProps): JSX.Element {
  const counts = lineCounts(claims, ledger);
  const rare = rareText(counts);
  const latest = latestSentence(claims, ledger, nameOf, titleOf);
  const paidMoney = `${signedUsdc(counts.paidAmount)} USDC`;
  const finding =
    agentCount === null
      ? "Agents search the site"
      : `${agentCount} agents search the site`;
  return (
    <section
      className="rv-line"
      data-testid="race-line"
      aria-label="Race summary"
    >
      <div className="rv-stage" aria-hidden="true">
        <div className="rv-track">
          <span className="rv-wire" />
          <svg
            className="rv-fork"
            viewBox="0 0 100 174"
            preserveAspectRatio="none"
          >
            <path d="M0 87 L100 29 M0 87 L100 87 M0 87 L100 145" />
          </svg>
          <Step n={1} name="Find">
            <span>
              {agentCount === null ? (
                finding
              ) : (
                <>
                  <span data-testid="line-agents">{agentCount}</span> agents
                  search the site
                </>
              )}
            </span>
          </Step>
          <Step n={2} name="Claim">
            <span>
              <span data-testid="line-claims">{counts.claims}</span> claims ·
              bond locked
            </span>
          </Step>
          <Step n={3} name="Check">
            <span>New? True? Fresh browser</span>
            {counts.checking > 0 && (
              <span className="rv-checking" data-testid="line-checking">
                {counts.checking} being checked
              </span>
            )}
          </Step>
        </div>
        <ul className="rv-results">
          <ResultRow
            kind="paid"
            name="Paid"
            count={counts.paid}
            note={paidMoney}
            testId="line-paid"
            noteTestId="line-paid-amount"
          />
          <ResultRow
            kind="lost"
            name="Bond lost"
            count={counts.lost}
            note="not true"
            testId="line-lost"
          />
          <ResultRow
            kind="repeat"
            name="Repeat"
            count={counts.repeat}
            note="bond back"
            testId="line-repeat"
          />
        </ul>
        {rare && (
          <p className="rv-rare" data-testid="line-rare">
            <i className="rv-swatch" />
            {rare}
          </p>
        )}
      </div>
      <ul className="rv-sr">
        <li>Step 1, Find: {finding}.</li>
        <li>Step 2, Claim: {counts.claims} claims, bond locked.</li>
        <li>
          Step 3, Check: new, true, in a fresh browser. {counts.checking} being
          checked.
        </li>
        <li>
          Paid: {counts.paid}, {usdc(counts.paidAmount)} USDC paid out.
        </li>
        <li>Bond lost: {counts.lost}, not true.</li>
        <li>Repeat: {counts.repeat}, bond back.</li>
        {rare && <li>{rare}.</li>}
      </ul>
      <div className="rv-latest">
        <span className="rv-latest-key">Latest</span>
        <p data-testid="line-latest" aria-live="polite">
          {latest ? (
            <Latest
              claims={claims}
              text={latest.text}
              claimId={latest.claimId}
            />
          ) : (
            "No result yet."
          )}
        </p>
        <a className="rv-see-all" href={seeAllHref} data-testid="line-see-all">
          See all {counts.claims} claims{" "}
          {seeAllHref.startsWith("#") ? "↓" : "→"}
        </a>
      </div>
    </section>
  );
}
