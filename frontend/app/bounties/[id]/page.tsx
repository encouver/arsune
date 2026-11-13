"use client";

import { ReactNode, use, useCallback, useEffect, useState } from "react";
import SiteHeader from "../../site-header";
import {
  api,
  bountyLabel,
  errorText,
  fundedTotal,
  FundingList,
  Options,
  OwnerBounty,
  perBug,
  settlementBadge,
  SiteCheck,
  siteName,
  usdc,
  utcTime,
} from "../api";
import { FundStep } from "../fund-step";
import {
  PHASE_STATUS,
  RacePhase,
  racePhase,
  RaceStep,
  useRace,
} from "../race-step";
import { Results, TechnicalDetails } from "../results";
import { Code, Message, ShowHide, SimTag } from "../ui";

const POLL_MS = 3000;
const FUNDING_POLL_MS = 5000;

type StepState = "done" | "active" | "locked" | "error";
type Refresh = () => Promise<void>;

const SITE_STATUS: Record<SiteCheck["state"], [StepState, string]> = {
  checking: ["active", "Loading your site…"],
  passed: ["done", "Done"],
  failed: ["error", "Could not load your site"],
};

function siteStep(b: OwnerBounty, proofDone: boolean): [StepState, string] {
  if (!proofDone) return ["locked", "After the proof"];
  if (b.site_check) return SITE_STATUS[b.site_check.state];
  return ["active", "Checked when a race starts"];
}

function fundStep(b: OwnerBounty): [StepState, string] {
  if (b.status === "pending_proof") return ["locked", "After your site loads"];
  if (fundedTotal(b) === 0) return ["active", "Not funded"];
  if (b.budget_amount < perBug(b)) return ["active", "Budget spent"];
  return ["done", "Funded"];
}

const RACE_STATE: Record<RacePhase, StepState> = {
  locked: "locked",
  loading: "active",
  offline: "error",
  other: "active",
  ready: "active",
  running: "active",
  finished: "done",
};

function lockedText(b: OwnerBounty): string {
  if (b.status === "pending_proof")
    return "First prove that you own the site. Arsune then loads it.";
  if (b.status === "paused")
    return "The bounty is paused. Check your site in step 2 to resume it.";
  if (b.status === "closed" && b.closed_reason === "budget_spent")
    return "The budget is spent. Add money in step 3 to open the bounty again.";
  if (b.status === "closed") return "This bounty is closed.";
  return "Fund the race in step 3 first.";
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
  const [ai, setAi] = useState(false);
  const race = useRace(id);

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
        <SiteHeader current="/races" />
        <main>
          <div className="sw">
            {loadError ? (
              <div className="sw-error" role="alert" data-testid="load-error">
                {loadError}{" "}
                {notFound && (
                  <a className="bo-link" href="/">
                    Start a new race
                  </a>
                )}
              </div>
            ) : (
              <p className="bo-muted">Loading the race…</p>
            )}
          </div>
        </main>
      </>
    );

  const dry = bounty.settlement_mode === "dry";
  // Arsune queues a site check only after the proof file matched.
  const proofDone = bounty.status !== "pending_proof" || !!bounty.site_check;
  const ready =
    bounty.status === "open" && bounty.budget_amount >= perBug(bounty);
  const phase = racePhase(race, bounty.id, ready);
  const site = siteStep(bounty, proofDone);
  const fund = fundStep(bounty);
  const glance: [string, StepState, string][] = [
    [
      "Prove you own the site",
      proofDone ? "done" : "active",
      proofDone ? "Done" : "Waiting for proof",
    ],
    ["Arsune loads your site", ...site],
    ["Fund the race", ...fund],
    ["Start the race", RACE_STATE[phase], PHASE_STATUS[phase]],
  ];

  return (
    <>
      <SiteHeader current="/races" />
      <main>
        <div className="sw">
          <div className="sw-head">
            <div>
              <p className="eyebrow">Bug race</p>
              <h1>
                <span className="sw-site">{siteName(bounty.url)}</span>
              </h1>
              <p className="sw-sub">
                {usdc(bounty.reward_amount)} USDC for each proven bug · agents
                bond {usdc(bounty.bond_amount)} USDC per claim
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
                {bountyLabel(bounty)}
              </span>
            </div>
          </div>
          {loadError && (
            <div className="sw-error" role="status">
              {loadError} Showing the last good state.
            </div>
          )}
          {bounty.status === "paused" && (
            <PausedBanner bounty={bounty} onChange={refresh} />
          )}

          <ol
            className="bo-strip"
            aria-label="Steps at a glance"
            data-testid="step-strip"
          >
            {glance.map(([title, state, status], i) => (
              <li key={title} data-step={state}>
                <a href={`#step-${i + 1}`}>
                  <span className="bo-strip-n" aria-hidden="true">
                    {state === "done" ? "✓" : i + 1}
                  </span>
                  <span>
                    <strong>{title}</strong>
                    <small>{status}</small>
                  </span>
                </a>
              </li>
            ))}
          </ol>

          <Results bounty={bounty} />

          <ol className="bo-steps" aria-label="Steps">
            <Step
              n={1}
              title="Prove that you own the site"
              state={proofDone ? "done" : "active"}
              status={proofDone ? "Done" : "Waiting for proof"}
              collapse
            >
              <ProveStep
                bounty={bounty}
                options={options}
                proofDone={proofDone}
                onChange={refresh}
              />
            </Step>
            <Step
              n={2}
              title="Arsune loads your site"
              state={site[0]}
              status={site[1]}
              collapse
            >
              <SiteStep
                bounty={bounty}
                proofDone={proofDone}
                onChange={refresh}
              />
            </Step>
            <Step n={3} title="Fund the race" state={fund[0]} status={fund[1]}>
              {fund[0] === "locked" ? (
                <p className="bo-muted">
                  You fund the race after Arsune loads your site.
                </p>
              ) : (
                <FundStep
                  bounty={bounty}
                  options={options}
                  funding={funding}
                  now={now}
                  onChange={refresh}
                />
              )}
            </Step>
            <Step
              n={4}
              title="Start the race"
              state={RACE_STATE[phase]}
              status={PHASE_STATUS[phase]}
            >
              <RaceStep
                bounty={bounty}
                race={race}
                phase={phase}
                lockedText={lockedText(bounty)}
                ai={ai}
                onAi={setAi}
              />
            </Step>
          </ol>

          <TechnicalDetails bounty={bounty} />
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
  collapse = false,
  children,
}: {
  n: number;
  title: string;
  state: StepState;
  status: string;
  // A done step that collapses shows one line; its body waits behind "Show".
  collapse?: boolean;
  children: ReactNode;
}) {
  const top = (
    <>
      <h2>
        <span className="bo-step-label">{n}.</span> {title}
      </h2>
      <span
        className={`sw-chip bo-chip-${state}`}
        data-testid={`step-${n}-status`}
      >
        {status}
      </span>
    </>
  );
  return (
    <li
      className="bo-step"
      id={`step-${n}`}
      data-step={state}
      data-testid={`step-${n}`}
    >
      <span className="bo-step-n" aria-hidden="true">
        {state === "done" ? "✓" : state === "error" ? "!" : n}
      </span>
      <section className="sw-panel" aria-label={title}>
        {collapse && state === "done" ? (
          <details className="bo-done" data-testid={`step-${n}-done`}>
            <summary className="sw-panel-top">
              {top}
              <ShowHide />
            </summary>
            {children}
          </details>
        ) : (
          <>
            <div className="sw-panel-top">{top}</div>
            {children}
          </>
        )}
      </section>
    </li>
  );
}

function ProveStep({
  bounty,
  options,
  proofDone,
  onChange,
}: {
  bounty: OwnerBounty;
  options: Options | null;
  proofDone: boolean;
  onChange: Refresh;
}) {
  const [busy, setBusy] = useState<"check" | "demo" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const isDemo = !!options && bounty.origin === options.demo_site_origin;

  if (proofDone)
    return (
      <Message
        kind="ok"
        testId="proof-done"
        text={`Ownership proven. Arsune read the token at ${bounty.proof_url}.`}
      />
    );

  async function verify() {
    // The proof is read now; the site check runs in Arsune's worker and the page polls it.
    await api(`/api/bounties/${bounty.id}/verify`, {});
    setInfo(null);
    await onChange();
  }

  async function run(kind: "check" | "demo") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "demo") {
        setInfo("Publishing the token on the demo site…");
        await api(`/api/bounties/${bounty.id}/demo-proof`, {});
        setInfo("The demo site serves the token. Checking ownership…");
      } else setInfo("Reading the proof file…");
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
      {isDemo && (
        <div className="bo-actions">
          <button
            type="button"
            className="bo-btn"
            onClick={() => run("demo")}
            disabled={!!busy}
          >
            {busy === "demo"
              ? "Setting proof…"
              : "Demo site: set proof automatically"}
          </button>
          <span className="bo-muted">
            This is the local demo shop, so Arsune can publish the token.
          </span>
        </div>
      )}
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
            Arsune reads the file once. Then it loads your site (step 2).
          </div>
        </li>
      </ol>
      <div className="bo-actions">
        <button
          type="button"
          className={`bo-btn${isDemo ? " bo-btn-quiet" : ""}`}
          onClick={() => run("check")}
          disabled={!!busy}
        >
          {busy === "check" ? "Checking…" : "Check ownership"}
        </button>
      </div>
      <Message kind="info" text={info} testId="proof-info" />
      <Message kind="error" text={error} testId="proof-error" />
    </>
  );
}

function SiteStep({
  bounty,
  proofDone,
  onChange,
}: {
  bounty: OwnerBounty;
  proofDone: boolean;
  onChange: Refresh;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = bounty.site_check;

  if (!proofDone)
    return (
      <p className="bo-muted">
        After the proof, Arsune loads your site in a fresh browser on desktop
        and mobile. Agents never start on a site that Arsune cannot check.
      </p>
    );

  async function again() {
    setBusy(true);
    setError(null);
    try {
      // An open bounty only needs a new check; otherwise verify reads the proof and checks.
      const path =
        bounty.status === "open" || bounty.status === "closed"
          ? "site-check"
          : "verify";
      await api(`/api/bounties/${bounty.id}/${path}`, {});
      await onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const checking = check?.state === "checking";
  return (
    <>
      <SiteCheckResult check={check} />
      <div className="bo-actions">
        <button
          type="button"
          className="bo-btn bo-btn-quiet"
          onClick={again}
          disabled={busy || checking}
        >
          {busy || checking
            ? "Loading your site…"
            : check
              ? "Check again"
              : "Check now"}
        </button>
      </div>
      <Message kind="error" text={error} testId="site-check-error" />
    </>
  );
}

function SiteCheckResult({ check }: { check: SiteCheck | null }) {
  if (!check)
    return (
      <p className="bo-muted" data-testid="site-check" data-state="none">
        Arsune loads your site again when a race starts, so agents never work on
        a site that it cannot check.
      </p>
    );
  const kind =
    check.state === "passed"
      ? "ok"
      : check.state === "failed"
        ? "error"
        : "info";
  const text =
    check.state === "checking"
      ? "Arsune is loading your site in a fresh browser, at 1280 and 390 pixels wide…"
      : check.reason;
  return (
    <div data-testid="site-check" data-state={check.state}>
      <Message kind={kind} text={text} />
      {check.state === "passed" && check.checked_at && (
        <p className="bo-muted">Checked {utcTime(check.checked_at)}.</p>
      )}
      {check.state === "failed" && (
        <p className="bo-muted">
          Agents cannot start until Arsune loads the page completely. Make the
          named files load, then check again.
        </p>
      )}
      <Message kind="error" text={check.note} testId="site-check-note" />
    </div>
  );
}

function PausedBanner({
  bounty,
  onChange,
}: {
  bounty: OwnerBounty;
  onChange: Refresh;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checking = bounty.site_check?.state === "checking";

  async function resume() {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/bounties/${bounty.id}/verify`, {});
      await onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="sw-panel bo-paused"
      aria-label="Paused"
      data-testid="paused-banner"
    >
      <div className="sw-error" role="alert">
        Arsune paused this bounty and takes no new claims, so agents do not
        spend time and tokens on claims that it cannot check.{" "}
        {bounty.paused_reason}
      </div>
      <div className="bo-actions">
        <button
          type="button"
          className="bo-btn"
          onClick={resume}
          disabled={busy || checking}
        >
          {busy || checking
            ? "Checking your site…"
            : "Check the site and resume"}
        </button>
      </div>
      <Message kind="error" text={error} testId="resume-error" />
    </section>
  );
}
