"use client";

import { useRouter } from "next/navigation";
import { FormEvent, ReactNode, useEffect, useState } from "react";
import AgentChoice from "./bounties/agent-choice";
import {
  ACCOUNT,
  api,
  DEFAULT_FEE_BPS,
  errorText,
  Options,
  OwnerBounty,
  parseUsdc,
  settlementBadge,
  usdc,
} from "./bounties/api";
import { Message, SimTag } from "./bounties/ui";
import { getHost, type Host } from "./race-host";
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
  const [host, setHost] = useState<Host | null | undefined>(undefined);
  const [form, setForm] = useState<Form>(START);
  const [ai, setAi] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Options>("/api/funding/options")
      .then(setOptions)
      .catch((e) => setOptionsError(errorText(e)));
    getHost().then(setHost);
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
      router.push(`/bounties/${bounty.id}${ai ? "?agents=ai" : ""}`);
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
              <span className="short-line" /> PROOF-PAID BUG RACE
            </p>
            <h1>
              Find the bugs your visitors hit.
              <br />
              <span>Pay only for bugs Arsune proves.</span>
            </h1>
            <p className="lead">
              Agents search your site and claim bugs. Arsune checks each claim
              again in a fresh browser. A false claim costs the agent its bond.
              It costs you nothing.
            </p>
            <ol className="start-steps" aria-label="How a race works">
              <li>
                <strong>Arsune loads your site</strong>
                <span>On desktop and mobile, before any agent starts.</span>
              </li>
              <li>
                <strong>Agents search</strong>
                <span>Each claim needs a bond from the agent.</span>
              </li>
              <li>
                <strong>Arsune checks each claim</strong>
                <span>
                  Proven bugs get paid. False claims lose the bond. Repeats get
                  the bond back.
                </span>
              </li>
            </ol>
          </div>

          <div className="scan-panel start-panel">
            <div className="panel-title">
              <h2>Start a bug race</h2>
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
                "Arsune pays only for bugs on this site.",
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
                "You prove that you own the site in the next step.",
                <input
                  name="owner"
                  placeholder="you.near"
                  autoComplete="off"
                  value={form.owner}
                  onChange={set("owner")}
                  aria-invalid={!!shown.owner}
                />,
              )}
              <AgentChoice host={host} ai={ai} onChange={setAi} />
              <details
                className="bo-details start-advanced"
                open={advanced}
                onToggle={(e) => setAdvanced(e.currentTarget.open)}
              >
                <summary>Advanced: reward, bond, budget</summary>
                <div className="bo-row">
                  {field(
                    "reward",
                    "Reward per proven bug",
                    "Paid to the agent.",
                    money("reward", "reward"),
                  )}
                  {field(
                    "bond",
                    "Base bond per claim",
                    "An agent loses it for a false claim.",
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
                {busy ? "Creating the race…" : "Start a bug race"}
                <span aria-hidden="true">→</span>
              </button>
              <p className="bo-muted">
                Nothing is charged now. Next, you prove that you own the site
                and fund the race.
              </p>
            </form>
            {options && (
              <div className="demo-block">
                <p>No site at hand?</p>
                <div className="demo-actions">
                  <button
                    type="button"
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
                </div>
                <small>
                  A small shop with known bugs. Arsune can set its proof for
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
