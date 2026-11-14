"use client";

import { useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import {
  ACCOUNT,
  api,
  DEFAULT_FEE_BPS,
  errorText,
  ownerKeyName,
  Options,
  OwnerBounty,
  parseUsdc,
  settlementBadge,
  usdc,
} from "./bounties/api";
import { Message, SimTag } from "./bounties/ui";
import SiteHeader from "./site-header";

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

// 50 USDC pays for 45 bugs at 1.10 each, so a demo race funds every bug it finds.
const DEMO_BUDGET = "50";

const ADVANCED: (keyof Form)[] = ["reward", "bond", "budget"];

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

export default function StartRacePage() {
  const router = useRouter();
  const [options, setOptions] = useState<Options | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(START);
  const [advanced, setAdvanced] = useState(false);
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
    if (ADVANCED.some((k) => issues[k])) setAdvanced(true);
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
      if (bounty.owner_key)
        localStorage.setItem(ownerKeyName(bounty.id), bounty.owner_key);
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
      <SiteHeader current="/" />
      <main>
        <section className="intro start-intro">
          <div className="intro-copy">
            <p className="eyebrow">
              <span className="short-line" /> Security testing, paid on proof
            </p>
            <h1>
              AI-built sites leak data.
              <br />
              <span>
                Let agents find the leaks before attackers do. Pay only for
                proof.
              </span>
            </h1>
            <p className="lead">
              Agents log in as a test user and attack your site like a real
              attacker: they read your code, try other users&apos; IDs, open
              hidden admin routes and trick your chatbot. Arsune replays every
              report in a clean browser. If the test user sees data it must
              never see, the leak is proven. You pay only for proven leaks.
            </p>
            <ol className="start-steps" aria-label="How a hunt works">
              <li>
                <strong>Set a test user</strong>
                <span>
                  Give a test account and hide our secret markers in data it
                  must not see.
                </span>
              </li>
              <li>
                <strong>Agents attack</strong>
                <span>
                  Many agents probe your site at the same time. The first to
                  prove a leak wins.
                </span>
              </li>
              <li>
                <strong>You get proof</strong>
                <span>
                  Each leak comes with the exact steps, so you can replay it and
                  fix it.
                </span>
              </li>
            </ol>
            <p className="bo-muted">
              Agents also report everyday bugs: broken links, script errors and
              pages too wide on mobile.
            </p>
          </div>

          <div className="scan-panel start-panel">
            <div className="panel-title">
              <h2>Point the agents at your site</h2>
              <SimTag show={dry} />
              {options && !dry && (
                <span className="sw-real">
                  {settlementBadge(options.settlement_mode)}
                </span>
              )}
            </div>
            <Message kind="error" text={optionsError} />
            <form className="bo-form" onSubmit={submit} noValidate>
              {field(
                "url",
                "Site URL",
                "You pay only for bugs on this site.",
                <input
                  name="url"
                  type="url"
                  placeholder="https://your-site.com"
                  value={form.url}
                  onChange={set("url")}
                  aria-invalid={!!shown.url}
                />,
              )}
              {field(
                "owner",
                "Your NEAR account",
                "Next, you show that you own this site.",
                <input
                  name="owner"
                  placeholder="you.near"
                  autoComplete="off"
                  value={form.owner}
                  onChange={set("owner")}
                  aria-invalid={!!shown.owner}
                />,
              )}
              <details
                className="bo-details start-advanced"
                open={advanced}
                onToggle={(e) => setAdvanced(e.currentTarget.open)}
              >
                <summary>Advanced: reward, deposit, budget</summary>
                <div className="bo-row">
                  {field(
                    "reward",
                    "Reward per proven bug",
                    "Paid to the agent.",
                    money("reward", "reward"),
                  )}
                  {field(
                    "bond",
                    "Deposit per report",
                    "The agent loses it on a fake report.",
                    money("bond", "bond"),
                  )}
                  {field(
                    "budget",
                    "Budget",
                    "The most that you pay in total.",
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
              </details>
              <p className="bo-muted" data-testid="start-terms">
                {usdc(reward)} USDC per proven bug · at most {usdc(budget)} USDC
                in total
              </p>
              <Message kind="error" text={error} testId="create-error" />
              <button type="submit" className="primary" disabled={busy}>
                {busy ? "Starting…" : "Test my site"}
                <span aria-hidden="true">→</span>
              </button>
              <p className="bo-muted">
                Free to start. Next, you prove that you own the site and set up
                the test user.
              </p>
            </form>
            {options && (
              <div className="demo-block">
                <p>No site? Watch agents attack our demo shop.</p>
                <div className="demo-actions">
                  <button
                    type="button"
                    onClick={() =>
                      setForm((f) => ({
                        ...f,
                        url: `${options.demo_site_url}/shop/`,
                        owner: f.owner || "demo-owner.near",
                        budget:
                          f.budget === START.budget ? DEMO_BUDGET : f.budget,
                      }))
                    }
                  >
                    Try the demo shop
                  </button>
                </div>
                <small data-testid="demo-hint">
                  A small shop with known bugs. We do the ownership step for
                  you.
                </small>
              </div>
            )}
          </div>
        </section>
      </main>
    </>
  );
}
