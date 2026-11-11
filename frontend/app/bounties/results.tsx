"use client";

import {
  claimGroup,
  claimPage,
  claimReason,
  titler,
  countGroups,
  countText,
  type Group,
  GROUP_ORDER,
  GROUPS,
} from "../claim-text";
import {
  LedgerRow,
  OwnerBounty,
  OwnerClaim,
  shortHash,
  usdc,
  utcTime,
} from "./api";
import { CopyButton } from "./ui";

const LEDGER_LABELS: Record<string, string> = {
  fund: "Money added",
  bond_in: "Bond posted",
  reward_out: "Reward to agent",
  bond_refund: "Bond returned",
  bond_forfeit: "Bond lost",
  fee: "Arsune fee",
  scout_share: "Scout share",
  withdraw: "Withdrawn",
};

/** The claims of a bounty, grouped by what Arsune decided, in plain words. */
export function Results({ bounty }: { bounty: OwnerBounty }) {
  const claims = bounty.claims;
  if (claims.length === 0) return null;
  const counts = countGroups(claims);
  const titleOf = titler(claims);
  const bonds = new Map(
    bounty.ledger
      .filter((r) => r.kind === "bond_in" && r.claim_id)
      .map((r) => [r.claim_id as string, r.amount]),
  );
  return (
    <section
      className="sw-panel bo-results"
      aria-label="Results"
      data-testid="results"
    >
      <div className="sw-panel-top">
        <h2>Results</h2>
        <span data-testid="results-summary">
          {claims.length} {claims.length === 1 ? "claim" : "claims"} ·{" "}
          {countText(counts)}
        </span>
      </div>
      {GROUP_ORDER.filter((g) => counts[g] > 0).map((g) => (
        <section
          key={g}
          className="bo-group"
          data-group={g}
          data-testid={`group-${g}`}
          aria-label={GROUPS[g].title}
        >
          <h3>
            {GROUPS[g].title} <span>{counts[g]}</span>
          </h3>
          <ul className="bo-result-list">
            {claims
              .filter((c) => claimGroup(c) === g)
              .map((c) => (
                <ClaimResult
                  key={c.id}
                  claim={c}
                  title={titleOf(c)}
                  group={g}
                  bond={bonds.get(c.id) ?? bounty.bond_amount}
                />
              ))}
          </ul>
        </section>
      ))}
    </section>
  );
}

function ClaimResult({
  claim: c,
  title,
  group,
  bond,
}: {
  claim: OwnerClaim;
  title: string;
  group: Group;
  bond: number;
}) {
  const reason = claimReason(c);
  return (
    <li
      className="bo-result"
      data-testid="claim-result"
      data-group={group}
      data-status={c.status}
    >
      <span className="bo-result-mark" data-group={group} aria-hidden="true">
        {GROUPS[group].mark}
      </span>
      <div className="bo-result-body">
        <div className="bo-result-top">
          <code className="bo-result-page">{claimPage(c)}</code>
          <strong data-testid="claim-title">{title}</strong>
        </div>
        {reason && (
          <p className="bo-result-reason" data-testid="claim-reason">
            {reason}
          </p>
        )}
        {c.status === "unfunded" && (
          <p className="bo-muted">
            Not paid: the budget ran out. The agent got its bond back.
          </p>
        )}
        <details className="bo-details" data-testid="claim-details">
          <summary>Details</summary>
          <dl className="bo-facts">
            <Fact label="Claim ID" value={c.id} />
            <Fact label="Agent account" value={c.hunter_account} />
            <Fact label="Bond" value={`${usdc(bond)} USDC`} />
            {c.settlement_note && (
              <Fact label="Settlement" value={c.settlement_note} />
            )}
            {c.scout && (
              <Fact
                label="Tip from scout"
                value={`${c.scout.account} (${c.scout.share_bps / 100}% of the reward)`}
              />
            )}
            {c.fingerprint && (
              <Fact label="Fault fingerprint" value={c.fingerprint} />
            )}
          </dl>
          {c.evidence_hash && (
            <div className="bo-code bo-hash">
              <code title={c.evidence_hash} data-testid="evidence-hash">
                sha256 {shortHash(c.evidence_hash)}
              </code>
              <CopyButton text={c.evidence_hash} label="evidence hash" />
            </div>
          )}
        </details>
      </div>
      {c.evidence_url && (
        <a
          className="bo-link bo-result-evidence"
          href={c.evidence_url}
          target="_blank"
          rel="noreferrer"
          data-testid="evidence-link"
        >
          Evidence
        </a>
      )}
    </li>
  );
}

export function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

/** IDs, hashes and every payment, kept out of the way of the results. */
export function TechnicalDetails({ bounty }: { bounty: OwnerBounty }) {
  // The UI forwards /api to Arsune, so this page's origin also serves the verify routes.
  const apiUrl =
    typeof window === "undefined" ? "<api>" : window.location.origin;
  return (
    <details className="sw-panel bo-tech" data-testid="technical-details">
      <summary>Technical details</summary>
      <dl className="bo-facts">
        <Fact label="Bounty ID" value={bounty.id} />
        <Fact label="Site" value={bounty.url} />
        <Fact label="Owner" value={bounty.owner_account} />
        <Fact
          label="Reward per proven bug"
          value={`${usdc(bounty.reward_amount)} USDC`}
        />
        <Fact
          label="Base bond per claim"
          value={`${usdc(bounty.bond_amount)} USDC`}
        />
        <Fact
          label={`Arsune fee (${bounty.fee_bps / 100}%)`}
          value={`${usdc(bounty.fee_amount)} USDC per proven bug`}
        />
        <Fact
          label="Planned budget"
          value={`${usdc(bounty.budget_target)} USDC`}
        />
        <Fact label="Settlement" value={bounty.settlement_mode} />
        <Fact label="Proof file" value={bounty.proof_url} />
        <Fact label="Created" value={utcTime(bounty.created_at)} />
      </dl>
      <p className="bo-muted" data-testid="verify-hint">
        Check a ruling yourself, from the Arsune repository:{" "}
        <code>uv run python -m app.verify {apiUrl} &lt;claim_id&gt;</code>
      </p>
      <h3>Payments</h3>
      {bounty.ledger.length === 0 ? (
        <p className="bo-muted">No payments yet.</p>
      ) : (
        <ul className="sw-ledger" data-testid="ledger">
          {[...bounty.ledger].reverse().map((r) => (
            <Payment key={r.id} row={r} />
          ))}
        </ul>
      )}
    </details>
  );
}

function Payment({ row: r }: { row: LedgerRow }) {
  return (
    <li data-kind={r.kind}>
      <span className="sw-kind">{LEDGER_LABELS[r.kind] ?? r.kind}</span>
      <span className="sw-acct" title={r.account}>
        {r.account}
      </span>
      <strong>{usdc(r.amount)} USDC</strong>
      {r.settlement_mode === "dry" ? (
        <span className="sw-sim sw-sim-small">Simulated</span>
      ) : r.explorer_url ? (
        <a href={r.explorer_url} target="_blank" rel="noreferrer">
          NearBlocks
        </a>
      ) : (
        <span className="sw-muted">{r.status}</span>
      )}
    </li>
  );
}
