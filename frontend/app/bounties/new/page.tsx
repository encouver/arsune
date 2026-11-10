"use client";

import { useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import SiteHeader from "../../site-header";
import {
  ACCOUNT,
  api,
  DEFAULT_FEE_BPS,
  errorText,
  Options,
  OwnerBounty,
  parseUsdc,
  usdc,
} from "../api";
import { Message, SimTag } from "../ui";

type Form = {
  url: string;
  owner: string;
  reward: string;
  bond: string;
  budget: string;
};

const START: Form = {
  url: "",
  owner: "",
  reward: "1",
  bond: "0.1",
  budget: "10",
};

function problems(form: Form): Partial<Record<keyof Form, string>> {
  const out: Partial<Record<keyof Form, string>> = {};
  const reward = parseUsdc(form.reward);
  const bond = parseUsdc(form.bond);
  const budget = parseUsdc(form.budget);
  if (!/^https?:\/\/\S+$/i.test(form.url.trim()))
    out.url = "Enter the full address, starting with https:// or http://.";
  if (!ACCOUNT.test(form.owner.trim()))
    out.owner = "Enter a NEAR account ID, for example you.near.";
  if (!reward) out.reward = "Enter an amount above 0, with at most 6 decimals.";
  if (!bond) out.bond = "Enter an amount above 0, with at most 6 decimals.";
  if (!budget) out.budget = "Enter an amount above 0, with at most 6 decimals.";
  else if (
    reward &&
    budget < reward + Math.floor((reward * DEFAULT_FEE_BPS) / 10_000)
  )
    out.budget = "The budget must cover at least one reward and its fee.";
  return out;
}

export default function NewBountyPage() {
  const router = useRouter();
  const [options, setOptions] = useState<Options | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(START);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Options>("/api/funding/options")
      .then(setOptions)
      .catch((e) => setOptionsError(errorText(e)));
  }, []);

  const issues = problems(form);
  const shown = touched ? issues : {};
  const reward = parseUsdc(form.reward) ?? 0;
  const fee = Math.floor((reward * DEFAULT_FEE_BPS) / 10_000);
  const budget = parseUsdc(form.budget) ?? 0;
  const bugs = reward ? Math.floor(budget / (reward + fee)) : 0;
  const dry = options?.settlement_mode === "dry";

  function set(key: keyof Form) {
    return (e: { target: { value: string } }) =>
      setForm((f) => ({ ...f, [key]: e.target.value }));
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setTouched(true);
    setError(null);
    if (Object.keys(issues).length) return;
    setBusy(true);
    try {
      const bounty = await api<OwnerBounty>("/api/bounties", {
        url: form.url.trim(),
        owner_account: form.owner.trim(),
        reward_amount: parseUsdc(form.reward),
        bond_amount: parseUsdc(form.bond),
        budget: parseUsdc(form.budget),
      });
      router.push(`/bounties/${bounty.id}`);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  function field(
    key: keyof Form,
    label: string,
    hint: string,
    input: ReactNode,
  ) {
    return (
      <label className="bo-field">
        {label}
        {input}
        {shown[key] ? (
          <small className="bo-invalid" id={`${key}-error`} role="alert">
            {shown[key]}
          </small>
        ) : (
          <small>{hint}</small>
        )}
      </label>
    );
  }

  function money(key: "reward" | "bond" | "budget", name: string) {
    return (
      <span className="bo-unit">
        <input
          name={name}
          inputMode="decimal"
          value={form[key]}
          onChange={set(key)}
          aria-invalid={!!shown[key]}
        />
        <span>USDC</span>
      </span>
    );
  }

  return (
    <>
      <SiteHeader current="/bounties/new" />
      <main>
        <div className="sw">
          <div className="sw-head">
            <div>
              <p className="eyebrow">Start a bounty</p>
              <h1>Pay only for bugs that Arsune proves</h1>
              <p className="sw-sub">
                Hunter agents search your site. Arsune checks each claim again
                in a fresh browser. False claims cost you nothing.
              </p>
            </div>
            <div className="sw-meta">
              <SimTag show={dry} />
              {options && !dry && (
                <span className="sw-real">
                  Real settlement via NEAR Intents
                </span>
              )}
            </div>
          </div>
          <Message kind="error" text={optionsError} />

          <div className="bo-grid">
            <section className="sw-panel" aria-label="New bounty">
              <div className="sw-panel-top">
                <h2>Your bounty</h2>
                {options && (
                  <button
                    type="button"
                    className="bo-btn bo-btn-quiet"
                    onClick={() =>
                      setForm((f) => ({
                        ...f,
                        url: `${options.demo_site_url}/shop/`,
                        owner: f.owner || "demo-owner.near",
                      }))
                    }
                  >
                    Use the local demo shop
                  </button>
                )}
              </div>
              <form className="bo-form" onSubmit={submit} noValidate>
                {field(
                  "url",
                  "Site URL",
                  "Arsune pays only for faults on this origin. You prove that you own it in the next step.",
                  <input
                    name="url"
                    type="url"
                    placeholder="https://your-site.example/"
                    value={form.url}
                    onChange={set("url")}
                    aria-invalid={!!shown.url}
                  />,
                )}
                {field(
                  "owner",
                  "Owner account",
                  "Your NEAR account ID. It is shown with the bounty.",
                  <input
                    name="owner"
                    placeholder="you.near"
                    autoComplete="off"
                    value={form.owner}
                    onChange={set("owner")}
                    aria-invalid={!!shown.owner}
                  />,
                )}
                <div className="bo-row">
                  {field(
                    "reward",
                    "Reward per proven bug",
                    "Paid to the hunter.",
                    money("reward", "reward"),
                  )}
                  {field(
                    "bond",
                    "Base bond per claim",
                    "A hunter loses it for a false claim.",
                    money("bond", "bond"),
                  )}
                  {field(
                    "budget",
                    "Budget",
                    "The total that you plan to fund.",
                    money("budget", "budget"),
                  )}
                </div>
                <div className="bo-preview" aria-label="Cost preview">
                  <div>
                    <span>
                      Arsune fee per proven bug ({DEFAULT_FEE_BPS / 100}%)
                    </span>
                    <strong data-testid="preview-fee">{usdc(fee)} USDC</strong>
                  </div>
                  <div>
                    <span>The budget pays for</span>
                    <strong data-testid="preview-bugs">
                      {bugs} proven bug{bugs === 1 ? "" : "s"}
                    </strong>
                  </div>
                </div>
                <Message kind="error" text={error} testId="create-error" />
                <div className="bo-actions">
                  <button type="submit" className="bo-btn" disabled={busy}>
                    {busy ? "Creating…" : "Create bounty"}
                  </button>
                  <span className="bo-muted">
                    Nothing is charged now. You fund the bounty after the proof.
                  </span>
                </div>
              </form>
            </section>

            <aside className="sw-panel" aria-label="How it works">
              <div className="sw-panel-top">
                <h2>How it works</h2>
              </div>
              <ol className="bo-how">
                <li>
                  <div>
                    <strong>Create</strong>
                    Set the reward, the base bond, and the budget.
                  </div>
                </li>
                <li>
                  <div>
                    <strong>Prove that you own the site</strong>
                    Publish a token at /.well-known/arsune-bounty.txt. Arsune
                    checks no claims before this proof.
                  </div>
                </li>
                <li>
                  <div>
                    <strong>Fund</strong>
                    {dry
                      ? "This Arsune runs in dry mode: fund with simulated money."
                      : "Send USDC with a signed NEAR Intents transfer, or pay from another chain through NEAR Intents 1Click."}
                  </div>
                </li>
                <li>
                  <div>
                    <strong>Watch the hunt</strong>
                    The live board shows each claim, the checker&apos;s verdict,
                    and every payment.
                  </div>
                </li>
              </ol>
            </aside>
          </div>
        </div>
      </main>
    </>
  );
}
