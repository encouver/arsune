"use client";

import {
  FormEvent,
  ReactNode,
  use,
  useCallback,
  useEffect,
  useState,
} from "react";
import SiteHeader from "../../site-header";
import {
  ACCOUNT,
  api,
  errorText,
  fundedFraction,
  FundingList,
  FundingRequest,
  Options,
  OwnerBounty,
  OwnerClaim,
  parseUsdc,
  settlementBadge,
  shortHash,
  Token,
  untilText,
  usdc,
  utcTime,
} from "../api";
import { Code, CopyButton, Message, SimTag } from "../ui";

const POLL_MS = 3000;
const FUNDING_POLL_MS = 5000;

const FUNDING_LABELS: Record<string, string> = {
  pending_deposit: "Waiting for your deposit",
  deposit_seen: "Deposit seen",
  processing: "Swapping into USDC",
  incomplete_deposit: "Deposit below the minimum",
  credited: "Credited to the bounty",
  refunded: "Refunded",
  failed: "Failed",
  expired: "Expired",
  needs_review: "Needs review",
};

const BOUNTY_LABELS: Record<string, string> = {
  pending_proof: "Waiting for proof",
  open: "Open",
  closed: "Closed",
};

const CHAINS: Record<string, string> = {
  near: "NEAR",
  eth: "Ethereum",
  base: "Base",
  arb: "Arbitrum",
  op: "Optimism",
  pol: "Polygon",
  bsc: "BNB Chain",
  avax: "Avalanche",
  gnosis: "Gnosis",
  sol: "Solana",
  btc: "Bitcoin",
  ton: "TON",
  tron: "TRON",
  sui: "Sui",
  xrp: "XRP Ledger",
  doge: "Dogecoin",
  zec: "Zcash",
  ltc: "Litecoin",
  bch: "Bitcoin Cash",
  stellar: "Stellar",
  cardano: "Cardano",
  aptos: "Aptos",
  bera: "Berachain",
  monad: "Monad",
};

type StepState = "done" | "active" | "locked";
type Refresh = () => Promise<void>;

function chain(id: string) {
  return CHAINS[id] ?? id.toUpperCase();
}

function newId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export default function BountyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [bounty, setBounty] = useState<OwnerBounty | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [options, setOptions] = useState<Options | null>(null);
  const [funding, setFunding] = useState<FundingList | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    try {
      setBounty(
        await api<OwnerBounty>(`/api/bounties/${encodeURIComponent(id)}`),
      );
      setLoadError(null);
    } catch (e) {
      setNotFound((e as { status?: number }).status === 404);
      setLoadError(errorText(e));
    }
    setNow(Date.now());
  }, [id]);

  const loadFunding = useCallback(async () => {
    try {
      setFunding(
        await api<FundingList>(
          `/api/bounties/${encodeURIComponent(id)}/funding`,
        ),
      );
    } catch (e) {
      setFunding((f) => (f ? { ...f, status_error: errorText(e) } : f));
    }
  }, [id]);

  useEffect(() => {
    api<Options>("/api/funding/options")
      .then(setOptions)
      .catch((e) => setLoadError(errorText(e)));
  }, []);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      await load();
      if (!stop) timer = setTimeout(tick, POLL_MS);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [load]);

  const live = options?.settlement_mode === "near_intents";
  useEffect(() => {
    if (!live) return;
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      await loadFunding();
      if (!stop) timer = setTimeout(tick, FUNDING_POLL_MS);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [live, loadFunding]);

  const refresh = useCallback(async () => {
    await Promise.all([load(), live ? loadFunding() : Promise.resolve()]);
  }, [load, loadFunding, live]);

  if (!bounty)
    return (
      <>
        <SiteHeader current="/bounties/new" />
        <main>
          <div className="sw">
            {loadError ? (
              <div className="sw-error" role="alert" data-testid="load-error">
                {loadError}{" "}
                {notFound && (
                  <a className="bo-link" href="/bounties/new">
                    Start a new bounty
                  </a>
                )}
              </div>
            ) : (
              <p className="bo-muted">Loading the bounty…</p>
            )}
          </div>
        </main>
      </>
    );

  const dry = bounty.settlement_mode === "dry";
  const proven = bounty.status !== "pending_proof";
  const funded = bounty.budget_amount > 0;
  const full = bounty.budget_amount >= bounty.budget_target;
  const states: StepState[] = [
    "done",
    proven ? "done" : "active",
    !proven ? "locked" : full ? "done" : "active",
    funded ? "active" : "locked",
  ];

  return (
    <>
      <SiteHeader current="/bounties/new" />
      <main>
        <div className="sw">
          <div className="sw-head">
            <div>
              <p className="eyebrow">Bounty for your site</p>
              <h1>
                <span className="sw-site">
                  {bounty.url.replace(/^https?:\/\//, "")}
                </span>
              </h1>
              <p className="sw-sub">
                {usdc(bounty.reward_amount)} USDC per proven bug ·{" "}
                {usdc(bounty.bond_amount)} USDC base bond per claim
              </p>
            </div>
            <div className="sw-meta">
              <SimTag show={dry} />
              {!dry && (
                <span className="sw-real">
                  {settlementBadge(bounty.settlement_mode)}
                </span>
              )}
              <span
                className={`sw-chip ${bounty.status === "open" ? "bo-chip-done" : "bo-chip-active"}`}
                data-testid="bounty-status"
              >
                {BOUNTY_LABELS[bounty.status] ?? bounty.status}
              </span>
            </div>
          </div>
          {loadError && (
            <div className="sw-error" role="status">
              {loadError} Showing the last good state.
            </div>
          )}

          <ol className="bo-steps" aria-label="Steps">
            <Step n={1} title="Bounty created" state={states[0]} status="Done">
              <dl className="bo-facts">
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
                  label="Budget"
                  value={`${usdc(bounty.budget_target)} USDC`}
                />
                <Fact label="Bounty ID" value={bounty.id} />
              </dl>
            </Step>
            <Step
              n={2}
              title="Prove that you own the site"
              state={states[1]}
              status={proven ? "Done" : "Waiting for proof"}
            >
              <ProveStep bounty={bounty} options={options} onChange={refresh} />
            </Step>
            <Step
              n={3}
              title="Fund the bounty"
              state={states[2]}
              status={
                !proven
                  ? "After the proof"
                  : full
                    ? "Funded"
                    : funded
                      ? "Partly funded"
                      : "Not funded"
              }
            >
              {proven ? (
                <FundStep
                  bounty={bounty}
                  options={options}
                  funding={funding}
                  now={now}
                  onChange={refresh}
                />
              ) : (
                <p className="bo-muted">
                  Funding opens after Arsune reads your proof file.
                </p>
              )}
            </Step>
            <Step
              n={4}
              title="Watch the hunt"
              state={states[3]}
              status={funded ? "Ready" : "After funding"}
            >
              <p className="bo-text">
                Hunter agents find open, funded bounties through the Arsune API.
                The live board shows each claim, the checker&apos;s verdict, and
                every payment for this bounty.
              </p>
              <a
                className="bo-board-link"
                href={`/swarm?bounty=${encodeURIComponent(bounty.id)}`}
                data-testid="board-link"
              >
                Open the live board →
              </a>
              <DecidedClaims claims={bounty.claims} />
            </Step>
          </ol>
        </div>
      </main>
    </>
  );
}

function Step({
  n,
  title,
  state,
  status,
  children,
}: {
  n: number;
  title: string;
  state: StepState;
  status: string;
  children: ReactNode;
}) {
  return (
    <li className="bo-step" data-step={state} data-testid={`step-${n}`}>
      <span className="bo-step-n" aria-hidden="true">
        {state === "done" ? "✓" : n}
      </span>
      <section className="sw-panel" aria-label={title}>
        <div className="sw-panel-top">
          <h2>{title}</h2>
          <span
            className={`sw-chip bo-chip-${state}`}
            data-testid={`step-${n}-status`}
          >
            {status}
          </span>
        </div>
        {children}
      </section>
    </li>
  );
}

type DecidedClaim = OwnerClaim & {
  evidence_hash: string;
  evidence_url: string;
};

function isDecided(c: OwnerClaim): c is DecidedClaim {
  return !!c.evidence_hash && !!c.evidence_url;
}

function DecidedClaims({ claims }: { claims: OwnerClaim[] }) {
  const decided = claims.filter(isDecided);
  if (decided.length === 0) return null;
  // The UI forwards /api to Arsune, so this page's origin also serves the verify routes.
  const apiUrl =
    typeof window === "undefined" ? "<api>" : window.location.origin;
  return (
    <div className="bo-evidence" data-testid="decided-claims">
      <h3>Decided claims and their evidence</h3>
      <ul className="bo-claims">
        {decided.map((c) => (
          <li key={c.id} data-testid="decided-claim" data-status={c.status}>
            <div className="bo-claim-top">
              <span className="sw-kind">{c.status.replace("_", " ")}</span>
              <span className="bo-muted">
                {c.claim_type} · {c.hunter_account} · claim {c.id}
              </span>
            </div>
            <div className="bo-claim-top">
              <div className="bo-code bo-hash">
                <code title={c.evidence_hash} data-testid="evidence-hash">
                  sha256 {shortHash(c.evidence_hash)}
                </code>
                <CopyButton text={c.evidence_hash} label="evidence hash" />
              </div>
              <a
                className="bo-link"
                href={c.evidence_url}
                target="_blank"
                rel="noreferrer"
                data-testid="evidence-link"
              >
                Evidence bundle
              </a>
            </div>
          </li>
        ))}
      </ul>
      <p className="bo-muted" data-testid="verify-hint">
        Check a ruling yourself, from the Arsune repository:{" "}
        <code>uv run python -m app.verify {apiUrl} &lt;claim_id&gt;</code>
      </p>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function ProveStep({
  bounty,
  options,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  onChange: Refresh;
}) {
  const [busy, setBusy] = useState<"check" | "demo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const isDemo = !!options && bounty.origin === options.demo_site_origin;

  if (bounty.status !== "pending_proof")
    return (
      <Message
        kind="ok"
        testId="proof-done"
        text={`Ownership proven. Arsune read the token at ${bounty.proof_url}.`}
      />
    );

  async function verify() {
    await api(`/api/bounties/${bounty.id}/verify`, {});
    setInfo("Ownership proven. The bounty is open.");
    await onChange();
  }

  async function check() {
    setBusy("check");
    setError(null);
    setInfo("Reading the proof file…");
    try {
      await verify();
    } catch (e) {
      setInfo(null);
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function demo() {
    setBusy("demo");
    setError(null);
    setInfo("Publishing the token on the demo site…");
    try {
      await api(`/api/bounties/${bounty.id}/demo-proof`, {});
      setInfo("The demo site serves the token. Checking ownership…");
      await verify();
    } catch (e) {
      setInfo(null);
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const command = `mkdir -p .well-known && printf '%s\\n' '${bounty.proof_token}' > .well-known/arsune-bounty.txt`;
  return (
    <>
      <ol className="bo-how">
        <li>
          <div>
            <strong>
              Publish this token as the whole content of the proof file
            </strong>
            <Code
              text={bounty.proof_token}
              label="proof token"
              testId="proof-token"
            />
          </div>
        </li>
        <li>
          <div>
            <strong>Serve it at this exact address</strong>
            <Code
              text={bounty.proof_url}
              label="proof file address"
              testId="proof-url"
            />
            <span className="bo-muted">
              Plain text, HTTP 200, at most 4 KB. Redirects must stay on{" "}
              {bounty.origin.replace(/:(80|443)$/, "")}. From your site&apos;s
              public folder:
            </span>
            <Code text={command} label="shell command" />
          </div>
        </li>
        <li>
          <div>
            <strong>Check ownership</strong>
            Arsune reads the file once under its URL policy and opens the bounty
            only when the content equals the token.
          </div>
        </li>
      </ol>
      <div className="bo-actions">
        <button
          type="button"
          className="bo-btn"
          onClick={check}
          disabled={!!busy}
        >
          {busy === "check" ? "Checking…" : "Check ownership"}
        </button>
        {isDemo && (
          <button
            type="button"
            className="bo-btn bo-btn-quiet"
            onClick={demo}
            disabled={!!busy}
          >
            {busy === "demo"
              ? "Setting proof…"
              : "Demo site: set proof automatically"}
          </button>
        )}
      </div>
      <Message kind="info" text={info} testId="proof-info" />
      <Message kind="error" text={error} testId="proof-error" />
    </>
  );
}

function FundStep({
  bounty,
  options,
  funding,
  now,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  funding: FundingList | null;
  now: number;
  onChange: Refresh;
}) {
  const remaining = Math.max(0, bounty.budget_target - bounty.budget_amount);
  const perBug = bounty.reward_amount + bounty.fee_amount;
  const [amountText, setAmountText] = useState(usdc(remaining || perBug));
  const amount = parseUsdc(amountText);
  const bugs = Math.floor(bounty.budget_amount / perBug);
  const funds = bounty.ledger.filter((r) => r.kind === "fund");

  return (
    <>
      <div>
        <div className="bo-meter" aria-hidden="true">
          <i
            style={{ width: `${Math.round(fundedFraction(bounty) * 100)}%` }}
          />
        </div>
        <p className="bo-text" data-testid="funded-total">
          Funded <strong>{usdc(bounty.budget_amount)}</strong> of{" "}
          {usdc(bounty.budget_target)} USDC · pays for {bugs} proven bug
          {bugs === 1 ? "" : "s"}
        </p>
        {bounty.status === "closed" &&
          bounty.closed_reason === "budget_spent" && (
            <p className="bo-text" data-testid="reopen-hint">
              The budget ran out, so the bounty is closed.{" "}
              {bounty.budget_amount < perBug
                ? `Add at least ${usdc(perBug - bounty.budget_amount)} USDC to open it again.`
                : "Any top-up opens it again."}
            </p>
          )}
      </div>
      {funds.length > 0 && (
        <ul className="sw-ledger" data-testid="fund-rows">
          {funds.map((r) => (
            <li key={r.id} data-kind="fund">
              <span className="sw-kind">Bounty funded</span>
              <span className="sw-acct" title={r.memo}>
                {r.memo}
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
          ))}
        </ul>
      )}
      <label className="bo-field" style={{ maxWidth: 320 }}>
        Amount to add
        <span className="bo-unit">
          <input
            name="fund-amount"
            inputMode="decimal"
            value={amountText}
            onChange={(e) => setAmountText(e.target.value)}
            aria-invalid={!amount}
          />
          <span>USDC</span>
        </span>
        <small className={amount ? undefined : "bo-invalid"}>
          {amount
            ? `${bounty.settlement_mode === "escrow" ? "The escrow contract" : "Arsune"} receives exactly this amount of USDC.`
            : "Enter an amount above 0, with at most 6 decimals."}
        </small>
      </label>
      <div className="bo-options">
        <CrossChainOption
          bounty={bounty}
          options={options}
          funding={funding}
          amount={amount}
          now={now}
          onChange={onChange}
        />
        <DirectOption
          bounty={bounty}
          options={options}
          amount={amount}
          onChange={onChange}
        />
      </div>
    </>
  );
}

function CrossChainOption({
  bounty,
  options,
  funding,
  amount,
  now,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  funding: FundingList | null;
  amount: number | null;
  now: number;
  onChange: Refresh;
}) {
  const cross = options?.crosschain;
  const tokens = cross?.tokens ?? [];
  const preferred =
    tokens.find((t) => t.symbol === "USDC" && t.blockchain === "base") ??
    tokens[0];
  const [asset, setAsset] = useState<string>("");
  const [refundTo, setRefundTo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chosen: Token | undefined =
    tokens.find((t) => t.asset_id === asset) ?? preferred;
  const requests = funding?.requests ?? [];

  if (!cross || !cross.available)
    return (
      <section
        className="bo-option"
        data-available="false"
        data-testid="crosschain-option"
      >
        <h3>From another chain (NEAR Intents 1Click)</h3>
        <div className="bo-unavailable" data-testid="crosschain-unavailable">
          <strong>Unavailable here</strong>
          {cross?.reason ?? "Loading the funding options…"}
        </div>
        <p className="bo-muted">
          On a live Arsune, 1Click gives you a deposit address on Ethereum,
          Base, Solana, Bitcoin and more. It swaps your payment into USDC and
          delivers it to Arsune inside NEAR Intents.
        </p>
      </section>
    );

  const groups = new Map<string, Token[]>();
  for (const t of tokens)
    groups.set(t.blockchain, [...(groups.get(t.blockchain) ?? []), t]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!amount || !chosen) return setError("Choose an asset and an amount.");
    if (!refundTo.trim())
      return setError(
        `Enter your ${chain(chosen.blockchain)} address for refunds.`,
      );
    setBusy(true);
    try {
      await api<FundingRequest>(`/api/bounties/${bounty.id}/funding-address`, {
        amount,
        origin_asset: chosen.asset_id,
        refund_to: refundTo.trim(),
      });
      await onChange();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="bo-option"
      data-available="true"
      data-testid="crosschain-option"
    >
      <h3>From another chain (NEAR Intents 1Click)</h3>
      <p className="bo-muted">
        Pay with any supported asset. 1Click swaps it and delivers exactly{" "}
        {amount ? usdc(amount) : "—"} USDC to Arsune inside NEAR Intents. The
        bounty is credited when 1Click reports success.
      </p>
      <form className="bo-form" onSubmit={create}>
        <label className="bo-field">
          Pay with
          <select
            name="origin-asset"
            value={chosen?.asset_id ?? ""}
            onChange={(e) => setAsset(e.target.value)}
          >
            {[...groups.entries()].map(([id, list]) => (
              <optgroup key={id} label={chain(id)}>
                {list.map((t) => (
                  <option key={t.asset_id} value={t.asset_id}>
                    {t.symbol} on {chain(t.blockchain)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="bo-field">
          Your refund address{chosen ? ` on ${chain(chosen.blockchain)}` : ""}
          <input
            name="refund-to"
            autoComplete="off"
            value={refundTo}
            onChange={(e) => setRefundTo(e.target.value)}
          />
          <small>
            1Click returns the deposit here if the swap cannot finish.
          </small>
        </label>
        <Message kind="error" text={error} testId="crosschain-error" />
        <button type="submit" className="bo-btn" disabled={busy || !amount}>
          {busy ? "Asking 1Click…" : "Get deposit address"}
        </button>
      </form>
      <Message kind="info" text={funding?.status_error ?? null} />
      {requests.map((r) => (
        <Deposit key={r.id} request={r} now={now} />
      ))}
    </section>
  );
}

function Deposit({
  request: r,
  now,
}: {
  request: FundingRequest;
  now: number;
}) {
  const passed = new Date(r.deadline).getTime() <= now;
  const where = `${r.origin_symbol} on ${chain(r.origin_chain)}`;
  return (
    <article
      className="bo-deposit"
      data-status={r.status}
      data-final={r.final}
      data-testid="deposit"
    >
      <div className="bo-deposit-top">
        <strong>
          {usdc(r.amount)} USDC from {where}
        </strong>
        <span
          className={`sw-chip ${r.status === "credited" ? "bo-chip-done" : r.final ? "bo-chip-locked" : "bo-chip-active"}`}
          data-testid="deposit-status"
        >
          {FUNDING_LABELS[r.status] ?? r.status}
        </span>
      </div>
      {!r.final && (
        <>
          <p className="bo-send">
            Send exactly{" "}
            <strong data-testid="deposit-amount">
              {r.amount_in_formatted} {r.origin_symbol}
            </strong>{" "}
            on {chain(r.origin_chain)} to:
          </p>
          <Code
            text={r.deposit_address}
            label="deposit address"
            testId="deposit-address"
          />
          {r.deposit_memo && (
            <>
              <span className="bo-muted">With this memo (required):</span>
              <Code text={r.deposit_memo} label="deposit memo" />
            </>
          )}
          {passed ? (
            <Message
              kind="error"
              text="The send-before time has passed. Do not send to this address; get a new one. Arsune still watches it for a deposit that was already sent."
            />
          ) : (
            <p className="bo-muted" data-testid="deposit-deadline">
              Send before {utcTime(r.deadline)} ({untilText(r.deadline, now)}).
              At least {r.min_amount_in_formatted} {r.origin_symbol} is needed;
              any extra is refunded to {r.refund_to}.
              {r.time_estimate
                ? ` About ${r.time_estimate} s after your deposit confirms.`
                : ""}
            </p>
          )}
        </>
      )}
      {r.status === "credited" && r.delivered_amount != null && (
        <Message
          kind="ok"
          testId="deposit-credited"
          text={`Credited ${usdc(r.delivered_amount)} USDC to the bounty.`}
        />
      )}
      {r.note && <p className="bo-muted">{r.note}</p>}
      <details className="bo-details">
        <summary>Details</summary>
        <span>1Click status: {r.oneclick_status ?? "not checked yet"}</span>
        <span>Deposit address: {r.deposit_address}</span>
        <span>Refund address: {r.refund_to}</span>
        <span>Asset: {r.origin_asset}</span>
        <span>Address closes: {utcTime(r.address_closes_at)}</span>
        {r.checked_at && <span>Last checked: {utcTime(r.checked_at)}</span>}
      </details>
    </article>
  );
}

function DirectOption({
  bounty,
  options,
  amount,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  amount: number | null;
  onChange: Refresh;
}) {
  const [fundId, setFundId] = useState(newId);
  const [sender, setSender] = useState("");
  const [signed, setSigned] = useState("");
  const [busy, setBusy] = useState<"sim" | "signed" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const dry = bounty.settlement_mode === "dry";
  const escrow = bounty.settlement_mode === "escrow";
  // An escrow deposit goes to the contract and names the bounty in its `msg`.
  const receiver =
    (escrow ? options?.escrow_contract : options?.arsune_account) ?? "…";
  const msg = escrow ? JSON.stringify({ fund: bounty.id }) : null;
  const token = options?.token ?? "…";
  const memo = `arsune:fund:${fundId}`;
  const units = amount ?? 0;
  const message = JSON.stringify({
    deadline: "<UTC time a few minutes ahead, e.g. 2026-11-09T19:36:32Z>",
    intents: [
      {
        intent: "transfer",
        memo,
        ...(msg ? { msg } : {}),
        receiver_id: receiver,
        tokens: { [token]: String(units) },
      },
    ],
    signer_id: sender.trim() || "<your account>",
  });

  async function simulate() {
    if (!amount) return;
    setBusy("sim");
    setError(null);
    setDone(null);
    try {
      const r = await api<{ sender: string; amount: number }>(
        `/api/bounties/${bounty.id}/fund-simulated`,
        { amount },
      );
      setDone(
        `Added ${usdc(r.amount)} USDC of simulated money, signed by throwaway account ${r.sender.slice(0, 12)}…`,
      );
      await onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function submitSigned(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setDone(null);
    if (!amount) return setError("Enter the amount first.");
    if (!ACCOUNT.test(sender.trim()))
      return setError("Enter the NEAR account that signed.");
    let intent: unknown;
    try {
      intent = JSON.parse(signed);
    } catch {
      return setError("The signed transfer is not valid JSON.");
    }
    setBusy("signed");
    try {
      await api(`/api/bounties/${bounty.id}/fund`, {
        fund_id: fundId,
        sender: sender.trim(),
        amount,
        signed_intent: intent,
      });
      setDone(`Arsune collected ${usdc(amount)} USDC with memo ${memo}.`);
      setFundId(newId());
      setSigned("");
      await onChange();
    } catch (err) {
      setError(errorText(err));
      // Only a 422 refused the request before the fund_id was spent.
      if ((err as { status?: number }).status !== 422) setFundId(newId());
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="bo-option" data-testid="direct-option">
      <h3>NEAR wallet: signed transfer</h3>
      {dry && (
        <div className="bo-form">
          <div className="bo-actions">
            <button
              type="button"
              className="bo-btn bo-btn-sim"
              onClick={simulate}
              disabled={!!busy || !amount}
            >
              {busy === "sim" ? "Funding…" : "Fund with simulated money"}
            </button>
            <SimTag />
          </div>
          <p className="bo-muted">
            Arsune signs a dry NEP-413 transfer with a throwaway key and checks
            it like a real one. No money moves.
          </p>
        </div>
      )}
      <Message kind="ok" text={done} testId="fund-done" />
      <Message kind="error" text={error} testId="fund-error" />
      <details className="bo-details" open={!dry}>
        <summary>Signed transfer instructions</summary>
        <p className="bo-text">
          Sign a NEP-413 <code>transfer</code> intent for{" "}
          {options?.intents_contract ?? "intents.near"}: exactly{" "}
          <strong>{units}</strong> units ({usdc(units)} USDC) of the bounty
          token to <strong>{receiver}</strong>, with this memo
          {msg ? " and this msg" : ""}:
        </p>
        <Code text={memo} label="memo" testId="fund-memo" />
        {msg && <Code text={msg} label="msg" testId="fund-msg" />}
        <span className="bo-muted">
          Message to sign (compact JSON, sorted keys):
        </span>
        <Code text={message} label="intent message" />
        <span className="bo-muted">
          Use a versioned nonce with the salt from{" "}
          <code>GET /api/settlement</code>. In Python:{" "}
          <code>
            {escrow
              ? "app.settlement.escrow.sign_deposit"
              : "app.settlement.near_intents.sign_transfer"}
          </code>
          . Then submit it here, or POST{" "}
          <code>{`{fund_id, sender, amount, signed_intent}`}</code> to{" "}
          <code>/api/bounties/{bounty.id}/fund</code>.
        </span>
        <form className="bo-form" onSubmit={submitSigned}>
          <label className="bo-field">
            Signer account
            <input
              name="sender"
              autoComplete="off"
              placeholder="you.near"
              value={sender}
              onChange={(e) => setSender(e.target.value)}
            />
          </label>
          <label className="bo-field">
            Signed transfer (JSON)
            <textarea
              name="signed-intent"
              value={signed}
              onChange={(e) => setSigned(e.target.value)}
              placeholder='{"standard":"nep413","payload":{…},"public_key":"ed25519:…","signature":"ed25519:…"}'
            />
          </label>
          <button
            type="submit"
            className="bo-btn bo-btn-quiet"
            disabled={!!busy || !signed.trim()}
          >
            {busy === "signed" ? "Submitting…" : "Submit signed transfer"}
          </button>
        </form>
      </details>
    </section>
  );
}
