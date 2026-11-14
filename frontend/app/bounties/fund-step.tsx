"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  ACCOUNT,
  api,
  errorText,
  fundedFraction,
  fundedTotal,
  FundingList,
  FundingRequest,
  Options,
  OwnerBounty,
  parseUsdc,
  perBug,
  Token,
  untilText,
  usdc,
  utcTime,
} from "./api";
import { Code, Message } from "./ui";
import {
  addIntentsKey,
  connectedAccount,
  connectWallet,
  depositToIntents,
  hasIntentsKey,
  disconnectWallet,
  intentsBalance,
  PreparedTransfer,
  signTransfer,
  WalletAccount,
  walletBalance,
} from "./wallet";

type Refresh = () => Promise<void>;

const FUNDING_LABELS: Record<string, string> = {
  pending_deposit: "Waiting for your deposit",
  deposit_seen: "Deposit seen",
  processing: "Swapping into USDC",
  incomplete_deposit: "Deposit below the minimum",
  credited: "Added to your budget",
  refunded: "Refunded",
  failed: "Failed",
  expired: "Expired",
  needs_review: "Needs review",
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

/** "Funded 50.00 USDC · pays for 38 more bugs · 42.60 USDC left" */
export function fundingText(b: OwnerBounty): string {
  const bugs = Math.floor(b.budget_amount / perBug(b));
  const total = fundedTotal(b);
  if (total === 0)
    return `Not funded yet. The planned budget is ${usdc(b.budget_target)} USDC.`;
  const more = b.budget_amount < total ? " more" : "";
  return `Funded ${usdc(total)} USDC · pays for ${bugs}${more} ${bugs === 1 ? "bug" : "bugs"} · ${usdc(b.budget_amount)} USDC left`;
}

export function FundStep({
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
  const cost = perBug(bounty);
  const remaining = Math.max(0, bounty.budget_target - bounty.budget_amount);
  const [amountText, setAmountText] = useState(usdc(remaining || cost));
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const amount = parseUsdc(amountText);
  const funds = bounty.ledger.filter((r) => r.kind === "fund");
  const dry = bounty.settlement_mode === "dry";

  async function simulate() {
    if (!amount) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const r = await api<{ amount: number }>(
        `/api/bounties/${bounty.id}/fund-simulated`,
        { amount },
      );
      setDone(`Added ${usdc(r.amount)} USDC of simulated money.`);
      await onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div>
        <p className="bo-text" data-testid="funded-total">
          {fundingText(bounty)}
        </p>
        <div className="bo-meter" aria-hidden="true">
          <i
            style={{ width: `${Math.round(fundedFraction(bounty) * 100)}%` }}
          />
        </div>
        {bounty.status === "closed" &&
          bounty.closed_reason === "budget_spent" && (
            <p className="bo-text" data-testid="reopen-hint">
              The budget ran out, so the hunt is closed.{" "}
              {bounty.budget_amount < cost
                ? `Add at least ${usdc(cost - bounty.budget_amount)} USDC to open it again.`
                : "Any top-up opens it again."}
            </p>
          )}
      </div>
      <div className="bo-fund-row">
        <label className="bo-field">
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
              ? `Each proven bug costs ${usdc(cost)} USDC: the reward and Arsune's fee.`
              : "Enter an amount above 0, with at most 6 decimals."}
          </small>
        </label>
        {dry && (
          <div className="bo-actions">
            <button
              type="button"
              className="bo-btn bo-btn-sim"
              onClick={simulate}
              disabled={busy || !amount}
            >
              {busy ? "Adding…" : "Add simulated money"}
            </button>
          </div>
        )}
      </div>
      <Message kind="ok" text={done} testId="fund-done" />
      <Message kind="error" text={error} testId="fund-error" />
      {funds.length > 0 && (
        <ul className="sw-ledger" data-testid="fund-rows">
          {funds.map((r) => (
            <li key={r.id} data-kind="fund">
              <span className="sw-kind" title={r.memo}>
                Money added
              </span>
              <strong>{usdc(r.amount)} USDC</strong>
              {r.settlement_mode === "dry" ? null : r.explorer_url ? (
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
      {/* No real money moves in a simulation, so only simulated money funds it. */}
      {!dry && (
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
      )}
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
        {amount ? usdc(amount) : "—"} USDC to Arsune inside NEAR Intents. Your
        budget goes up when 1Click reports success.
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
          text={`Added ${usdc(r.delivered_amount)} USDC to your budget.`}
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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
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

  async function fund(from: string, signedIntent: unknown, id: string) {
    await api(`/api/bounties/${bounty.id}/fund`, {
      fund_id: id,
      sender: from,
      amount,
      signed_intent: signedIntent,
    });
    setDone(
      `Arsune collected ${usdc(units)} USDC with memo arsune:fund:${id}.`,
    );
    setFundId(newId());
  }

  function failed(err: unknown) {
    setError(errorText(err));
    // Only a 422 refused the request before the fund_id was spent.
    if ((err as { status?: number }).status !== 422) setFundId(newId());
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
    setBusy(true);
    try {
      await fund(sender.trim(), intent, fundId);
      setSigned("");
      await onChange();
    } catch (err) {
      failed(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="bo-option" data-testid="direct-option">
      <h3>NEAR wallet: signed transfer</h3>
      <WalletFund
        bounty={bounty}
        options={options}
        amount={amount}
        fundId={fundId}
        fund={fund}
        failed={failed}
        setError={setError}
        setDone={setDone}
        onConnect={setSender}
        onChange={onChange}
      />
      <Message kind="ok" text={done} testId="signed-done" />
      <Message kind="error" text={error} testId="signed-error" />
      <details className="bo-details">
        <summary>Advanced: paste a signed transfer</summary>
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
          <code>GET /api/settlement</code>, or get the exact message and nonce
          from <code>POST /api/bounties/{bounty.id}/fund-intent</code>. In
          Python:{" "}
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
            disabled={busy || !signed.trim()}
          >
            {busy ? "Submitting…" : "Submit signed transfer"}
          </button>
        </form>
      </details>
    </section>
  );
}

// Connect -> (move USDC into NEAR Intents) -> sign and fund. Each step shows when it is next.
function WalletFund({
  bounty,
  options,
  amount,
  fundId,
  fund,
  failed,
  setError,
  setDone,
  onConnect,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  amount: number | null;
  fundId: string;
  fund: (from: string, signedIntent: unknown, id: string) => Promise<void>;
  failed: (err: unknown) => void;
  setError: (text: string | null) => void;
  setDone: (text: string | null) => void;
  onConnect: (accountId: string) => void;
  onChange: Refresh;
}) {
  const [account, setAccount] = useState<WalletAccount | null>(null);
  const [inIntents, setInIntents] = useState<bigint | null>(null);
  const [inWallet, setInWallet] = useState<bigint | null>(null);
  const [step, setStep] = useState<string | null>(null);
  // false: NEAR Intents does not know the wallet key yet. null: not checked.
  const [keyKnown, setKeyKnown] = useState<boolean | null>(null);
  const [keyText, setKeyText] = useState<string | null>(null);
  // A signed transfer kept while the owner allows the key. It is valid until its deadline.
  const [held, setHeld] = useState<{
    id: string;
    intent: { public_key: string };
    deadline: number;
  } | null>(null);
  // A move was sent for `need` units. It stays here until NEAR Intents shows the balance.
  const [moved, setMoved] = useState<{
    tx: string | null;
    need: bigint;
    timedOut: boolean;
  } | null>(null);
  // Single flight: no second wallet action while one runs, and no second move per amount.
  const working = useRef(false);
  const movedFor = useRef<string | null>(null);
  const intents = options?.intents_contract;
  const token = options?.token;

  useEffect(() => {
    let live = true;
    connectedAccount().then((a) => {
      if (live && a) {
        setAccount(a);
        onConnect(a.accountId);
      }
    });
    return () => {
      live = false;
    };
    // Restore an earlier connection once.
  }, []);

  const loadBalances = useCallback(async () => {
    if (!account || !intents || !token) return;
    try {
      const [a, b] = await Promise.all([
        intentsBalance(intents, account.accountId, token),
        walletBalance(account.accountId, token).catch(() => null),
      ]);
      setInIntents(a);
      setInWallet(b);
    } catch {
      setError("Could not read your USDC balance from NEAR. Try again.");
    }
  }, [account, intents, token, setError]);

  useEffect(() => {
    loadBalances();
  }, [loadBalances]);

  // Wallets that share their key before signing: ask NEAR Intents right after connecting.
  useEffect(() => {
    const key = account?.publicKey;
    if (!account || !key || !intents) return;
    setKeyText(key);
    hasIntentsKey(intents, account.accountId, key)
      .then(setKeyKnown)
      .catch(() => {});
  }, [account, intents]);

  async function run(name: string, work: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setStep(name);
    setError(null);
    setDone(null);
    try {
      await work();
    } catch (err) {
      if (name === "fund") failed(err);
      else setError(errorText(err));
    } finally {
      working.current = false;
      setStep(null);
    }
  }

  const connect = () =>
    run("connect", async () => {
      const a = await connectWallet();
      setAccount(a);
      onConnect(a.accountId);
    });

  const disconnect = () =>
    run("disconnect", async () => {
      await disconnectWallet().catch(() => {});
      setAccount(null);
      setInIntents(null);
      setInWallet(null);
      setKeyKnown(null);
      setKeyText(null);
      setHeld(null);
    });

  const need = amount ? BigInt(amount) : BigInt(0);
  const short = inIntents === null ? BigInt(0) : need - inIntents;

  // Poll until NEAR Intents shows `target`; the RPC lags behind the transaction.
  async function balanceReaches(target: bigint) {
    if (!account || !intents || !token) return false;
    for (let i = 0; i < 30; i++) {
      try {
        const now = await intentsBalance(intents, account.accountId, token);
        setInIntents(now);
        if (now >= target) return true;
      } catch {
        // Try again on the next round.
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    return false;
  }

  async function watchMove(tx: string | null) {
    setMoved({ tx, need, timedOut: false });
    if (await balanceReaches(need)) setMoved(null);
    else setMoved({ tx, need, timedOut: true });
  }

  const move = () => {
    if (movedFor.current === need.toString()) return;
    return run("move", async () => {
      if (!account || !intents || !token) return;
      const walletBefore = inWallet;
      let tx: string | null;
      try {
        tx = await depositToIntents(account, intents, token, short);
      } catch (err) {
        // The wallet may close after sending. If the wallet balance fell, the move happened.
        const after = await walletBalance(account.accountId, token).catch(
          () => null,
        );
        if (walletBefore === null || after === null || after >= walletBefore)
          throw err;
        movedFor.current = need.toString();
        await watchMove(null);
        return;
      }
      movedFor.current = need.toString();
      await watchMove(tx);
    });
  };

  const signAndFund = () =>
    run("fund", async () => {
      if (!account || !amount) return;
      const id = fundId;
      const prepared = await api<PreparedTransfer>(
        `/api/bounties/${bounty.id}/fund-intent`,
        { fund_id: id, sender: account.accountId, amount },
      );
      const signedIntent = await signTransfer(account, prepared);
      if (intents && keyKnown !== true) {
        const known = await hasIntentsKey(
          intents,
          account.accountId,
          signedIntent.public_key,
        );
        setKeyKnown(known);
        if (!known) {
          setKeyText(signedIntent.public_key);
          setHeld({
            id,
            intent: signedIntent,
            deadline: Date.parse(prepared.deadline),
          });
          return;
        }
      }
      await fund(account.accountId, signedIntent, id);
      movedFor.current = null;
      await Promise.all([loadBalances(), onChange()]);
    });

  // Registering the key is a transaction; the RPC can lag a moment behind it.
  async function keyAppears(pk: string) {
    if (!account || !intents) return false;
    for (let i = 0; i < 8; i++) {
      if (await hasIntentsKey(intents, account.accountId, pk)) return true;
      await new Promise((r) => setTimeout(r, 1500));
    }
    return false;
  }

  const allowKey = () =>
    run("key", async () => {
      if (!account || !intents || !keyText) return;
      await addIntentsKey(account, intents, keyText);
      if (!(await keyAppears(keyText)))
        throw new Error(
          "The key is not visible in NEAR Intents yet. Wait a moment and try again.",
        );
      setKeyKnown(true);
      const kept = held;
      setHeld(null);
      if (kept && kept.deadline - Date.now() > 15_000) {
        try {
          await fund(account.accountId, kept.intent, kept.id);
          await Promise.all([loadBalances(), onChange()]);
        } catch (err) {
          failed(err);
        }
      } else if (kept) {
        setDone(
          "The wallet key is allowed. The signed transfer expired. Click Sign and fund again.",
        );
      }
    });

  if (!account)
    return (
      <div className="bo-actions">
        <button
          type="button"
          className="bo-btn"
          onClick={connect}
          disabled={step !== null || !options}
          data-testid="wallet-connect"
        >
          {step === "connect"
            ? "Waiting for the wallet…"
            : "Connect NEAR wallet"}
        </button>
      </div>
    );

  return (
    <div className="bo-form" data-testid="wallet-fund">
      <p className="bo-muted" data-testid="wallet-account">
        Connected as <strong>{account.accountId}</strong>.{" "}
        <button
          type="button"
          className="bo-link"
          onClick={disconnect}
          disabled={step !== null}
        >
          Disconnect
        </button>
      </p>
      <p className="bo-muted" data-testid="intents-balance">
        Your USDC in NEAR Intents:{" "}
        {inIntents === null ? "checking…" : usdc(Number(inIntents))}
      </p>
      {!amount || keyKnown !== false ? null : (
        <>
          <button
            type="button"
            className="bo-btn"
            onClick={allowKey}
            disabled={step !== null || !keyText}
            data-testid="wallet-allow-key"
          >
            {step === "key"
              ? "Waiting for the wallet…"
              : "Allow this wallet key in NEAR Intents (one time)"}
          </button>
          <span className="bo-muted">
            NEAR Intents does not know this wallet key yet. One transaction (1
            yoctoNEAR and gas){" "}
            {held
              ? "sends your signed transfer after it."
              : keyText
                ? ""
                : "after you sign."}
          </span>
        </>
      )}
      {!amount || keyKnown === false || inIntents === null ? null : short >
          BigInt(0) && moved?.need === need ? (
        <p className="bo-muted" data-testid="wallet-moving">
          {moved.timedOut ? (
            <>
              The move was sent
              {moved.tx && (
                <>
                  {" "}
                  (tx{" "}
                  <a
                    href={`https://nearblocks.io/txns/${moved.tx}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {moved.tx.slice(0, 8)}…
                  </a>
                  )
                </>
              )}
              . Wait a moment and reload; do not move again.
            </>
          ) : (
            <>
              Moving… waiting for NEAR Intents to show it
              {moved.tx && (
                <>
                  {" "}
                  (tx{" "}
                  <a
                    href={`https://nearblocks.io/txns/${moved.tx}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {moved.tx.slice(0, 8)}…
                  </a>
                  )
                </>
              )}
              .
            </>
          )}
        </p>
      ) : short > BigInt(0) ? (
        <>
          <button
            type="button"
            className="bo-btn"
            onClick={move}
            disabled={step !== null}
            data-testid="wallet-move"
          >
            {step === "move"
              ? "Waiting for the wallet…"
              : `Move ${usdc(Number(short))} USDC into NEAR Intents`}
          </button>
          <span className="bo-muted">
            One transaction from your wallet (1 yoctoNEAR and gas).
            {inWallet !== null &&
              ` Your wallet holds ${usdc(Number(inWallet))} USDC.`}
          </span>
        </>
      ) : (
        <button
          type="button"
          className="bo-btn"
          onClick={signAndFund}
          disabled={step !== null}
          data-testid="wallet-fund-button"
        >
          {step === "fund"
            ? "Signing and funding…"
            : `Sign and fund ${usdc(amount)} USDC`}
        </button>
      )}
    </div>
  );
}
