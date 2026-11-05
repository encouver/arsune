"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type Mode = "scripted" | "model";
type FindingState = "confirmed" | "rejected" | "unverified";
type Evidence = {
  url?: string;
  screenshot?: string;
  screenshot_ref?: string;
  viewport?: { width: number; height: number };
  captured_at?: string;
  observations?: unknown;
  text?: string;
  selector?: string;
};
type Finding = {
  id: string;
  status: FindingState;
  role: string;
  claim_type: string;
  explanation?: string;
  title?: string;
  urls?: string[];
  affected_urls?: string[];
  expected_behavior?: string;
  observed_behavior?: string;
  reproduction_steps?: string[];
  visitor_impact?: string;
  suggested_correction?: string;
  reason?: string;
  checker_reason?: string;
  evidence?: Evidence[];
  candidate?: Partial<Finding>;
  checker?: { reason?: string; evidence?: Evidence[] };
  demo_injected?: boolean;
  injected?: boolean;
};
type Coverage = {
  page_limit?: number;
  selected_count?: number;
  roles?: string[];
  requests?: number;
  blocked_requests?: number;
  scope?: string;
  external_resources?: string;
  redirect_policy?: string;
  browser_limits?: string;
  consistency_scope?: string;
  incomplete_pages?: string[];
  redirects?: {
    url: string;
    target: string;
    resource_type: string;
    status: number;
    allowed: boolean;
    reason?: string;
  }[];
  resource_issues?: {
    url: string;
    page_url: string;
    resource_type: string;
    reason: string;
  }[];
  incomplete_layout_pages?: string[];
};
type Report = {
  findings?: Finding[];
  selected_pages?: string[];
  inspected_pages?: string[];
  viewports?: Record<string, { width: number; height: number }>;
  coverage?: Coverage;
  observations?: { url: string; final_url?: string }[];
  incomplete_checks?: unknown[];
  mode?: string;
  provider?: unknown;
  started_at?: string;
  completed_at?: string;
  [key: string]: unknown;
};
type Job = {
  id: string;
  url: string;
  mode: Mode;
  status: "queued" | "running" | "completed" | "failed";
  progress: { stage: string; message: string };
  events: { id: string; role: string; message: string; created_at: string }[];
  report: Report | null;
  findings: Finding[];
  error: string | null;
  created_at: string;
  updated_at: string;
};
type Config = {
  modes: string[];
  demo_urls: { faulty: string; corrected: string };
  max_pages: number;
};
const roles = ["navigation", "mobile", "consistency", "checker"];
const names: Record<string, string> = {
  navigation: "Navigation",
  mobile: "Mobile layout",
  consistency: "Page consistency",
  checker: "Independent checker",
  coordinator: "Coordinator",
};

function displayMessage(message: string): string {
  return message
    .replaceAll("broken_link", "broken link")
    .replaceAll("horizontal_overflow", "mobile overflow")
    .replaceAll("price_mismatch", "price consistency");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(
      "The check service did not return a valid response. Try again.",
    );
  }
  if (!response.ok) {
    const detail = body.detail || body.error;
    throw new Error(
      typeof detail === "string"
        ? detail
        : "The check service could not accept this request.",
    );
  }
  return body as T;
}

function readable(value: unknown): string {
  if (value === undefined || value === null) return "Not recorded";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(readable).join(" · ");
  if (typeof value === "object")
    return Object.entries(value)
      .map(([key, item]) => `${key.replaceAll("_", " ")}: ${readable(item)}`)
      .join("; ");
  return String(value);
}

function CoveragePanel({
  report,
  pages,
  mode,
}: {
  report: Report;
  pages: string[];
  mode: Mode;
}) {
  const provider = report.provider as
    { name?: string; live_call?: boolean } | undefined;
  const coverage = report.coverage;
  const addresses = report.observations?.filter(
    (item, index, all) =>
      item.final_url &&
      all.findIndex(
        (other) => other.url === item.url && other.final_url === item.final_url,
      ) === index,
  );
  return (
    <div className="coverage">
      <h3>What this check covered</h3>
      <p>
        <strong>Mode:</strong>{" "}
        {mode === "scripted"
          ? "Scripted browser checks"
          : "Model-assisted checks"}
      </p>
      <p>
        <strong>Provider:</strong>{" "}
        {typeof report.provider === "string"
          ? report.provider
          : provider?.name || "Not recorded"}
        {provider?.live_call === true
          ? " · Live model call"
          : mode === "model"
            ? " · No live model call recorded"
            : ""}
      </p>
      <h4>Inspected pages ({pages.length})</h4>
      {pages.length ? (
        <ul>
          {pages.map((page, index) => (
            <li key={index} className="url">
              {page}
            </li>
          ))}
        </ul>
      ) : (
        <p>No page list was recorded.</p>
      )}
      {!!addresses?.length && (
        <>
          <h4>Page addresses</h4>
          <ul>
            {addresses.map((item, index) => (
              <li key={index}>
                <strong>Original URL:</strong>{" "}
                <span className="url">{item.url}</span>
                <br />
                <strong>Final URL:</strong>{" "}
                <span className="url">{item.final_url}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <h4>Viewports</h4>
      {report.viewports ? (
        <ul>
          {Object.entries(report.viewports).map(([name, size]) => (
            <li key={name}>
              {name === "mobile" ? "Mobile" : "Desktop"}: {size.width} ×{" "}
              {size.height} pixels
            </li>
          ))}
        </ul>
      ) : (
        <p>No viewport sizes were recorded.</p>
      )}
      <h4>Checks performed</h4>
      <p>
        {coverage?.roles?.map((role) => names[role] || role).join(" · ") ||
          "No check list was recorded."}
      </p>
      <p>{coverage?.scope || "Results apply only to the selected pages."}</p>
      {coverage?.consistency_scope && (
        <p>
          This version compares only values that the site marks as the same
          fact. General page text is not checked for consistency.
        </p>
      )}
      {coverage?.redirect_policy && <p>{coverage.redirect_policy}</p>}
      {coverage?.browser_limits && <p>{coverage.browser_limits}</p>}
      {coverage?.external_resources && (
        <p className="notice">{coverage.external_resources}</p>
      )}
      {coverage?.redirects && (
        <>
          <h4>Redirect history</h4>
          {coverage.redirects.length ? (
            <ul>
              {coverage.redirects.map((item, index) => (
                <li key={index}>
                  <strong>{item.allowed ? "Followed" : "Blocked"}:</strong>{" "}
                  <span className="url">{item.url}</span> →{" "}
                  <span className="url">{item.target}</span> (HTTP {item.status}
                  ){item.reason && <>. {item.reason}</>}
                </li>
              ))}
            </ul>
          ) : (
            <p>No redirects were recorded.</p>
          )}
        </>
      )}
      {coverage?.resource_issues && (
        <>
          <h4>Missing or blocked resources</h4>
          {coverage.resource_issues.length ? (
            <>
              <p>
                These files could not load. Page appearance can be incomplete.
              </p>
              <ul>
                {coverage.resource_issues.map((item, index) => (
                  <li key={index}>
                    <span className="url">{item.url}</span>: {item.reason}
                    <br />
                    Page: <span className="url">{item.page_url}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p>No missing or blocked resources were recorded.</p>
          )}
        </>
      )}
      <h4>Incomplete checks</h4>
      {!!coverage?.incomplete_layout_pages?.length && (
        <>
          <p>
            Mobile layout checks are incomplete for these pages because required
            files did not load. These pages have not passed the layout check.
          </p>
          <ul>
            {coverage.incomplete_layout_pages.map((page) => (
              <li key={page} className="url">
                {page}
              </li>
            ))}
          </ul>
        </>
      )}
      {coverage?.incomplete_pages?.length ? (
        <>
          <p>
            These pages could not be fully inspected. This does not mean that
            they passed.
          </p>
          <ul>
            {coverage.incomplete_pages.map((page) => (
              <li key={page} className="url">
                {page}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>No incomplete page loads were recorded.</p>
      )}
      {report.incomplete_checks?.map((item, index) => (
        <p key={index}>{readable(item)}</p>
      ))}
      <p>
        Site repairs are performed by you or your website builder. Start a new
        check after repairs to create a separate report.
      </p>
    </div>
  );
}

function FindingCard({
  finding: raw,
  jobId,
}: {
  finding: Finding;
  jobId: string;
}) {
  const finding = { ...raw.candidate, ...raw };
  const evidence = raw.checker?.evidence || raw.evidence || [];
  const reason = raw.checker?.reason || raw.checker_reason || raw.reason;
  return (
    <article className="finding">
      <div className="finding-top">
        <span className={`badge ${finding.status}`}>{finding.status}</span>
        <span className="eyebrow">{names[finding.role] || finding.role}</span>
        {(finding.demo_injected || finding.injected) && (
          <span className="demo-label">Demo-only claim</span>
        )}
      </div>
      <h3>
        {finding.title ||
          finding.explanation ||
          finding.claim_type.replaceAll("_", " ")}
      </h3>
      {finding.title && finding.explanation && <p>{finding.explanation}</p>}
      {reason && (
        <p className="check-reason">
          <strong>Checker result:</strong> {reason}
        </p>
      )}
      <div className="finding-details">
        {finding.visitor_impact && (
          <div>
            <h4>Visitor impact</h4>
            <p>{finding.visitor_impact}</p>
          </div>
        )}
        {finding.suggested_correction && (
          <div>
            <h4>Suggested correction</h4>
            <p>{finding.suggested_correction}</p>
          </div>
        )}
        {finding.expected_behavior && (
          <div>
            <h4>Expected</h4>
            <p>{finding.expected_behavior}</p>
          </div>
        )}
        {finding.observed_behavior && (
          <div>
            <h4>Observed</h4>
            <p>{finding.observed_behavior}</p>
          </div>
        )}
      </div>
      {(finding.affected_urls || finding.urls || []).map((url) => (
        <a
          className="url"
          key={url}
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          {url} ↗
        </a>
      ))}
      <details>
        <summary>Evidence and steps to repeat</summary>
        {finding.reproduction_steps?.length ? (
          <ol>
            {finding.reproduction_steps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        ) : (
          <p>No steps were recorded.</p>
        )}
        {evidence.map((item, index) => {
          const shot = item.screenshot || item.screenshot_ref;
          const source = shot
            ? `/api/jobs/${encodeURIComponent(jobId)}/evidence/${encodeURIComponent(shot.split("/").pop()!)}`
            : null;
          return (
            <figure key={index}>
              {item.url && <p className="url">{item.url}</p>}
              {(item.observations !== undefined || item.text) && (
                <p>{readable(item.observations || item.text)}</p>
              )}
              {source && (
                <a href={source} target="_blank" rel="noreferrer">
                  <img
                    src={source}
                    alt={`Evidence for ${finding.title || finding.claim_type}`}
                    loading="lazy"
                  />
                </a>
              )}
              <figcaption>
                {item.viewport
                  ? `${item.viewport.width} × ${item.viewport.height}`
                  : "Viewport not recorded"}
                {item.captured_at
                  ? ` · ${new Date(item.captured_at).toLocaleString()}`
                  : ""}
              </figcaption>
            </figure>
          );
        })}
      </details>
    </article>
  );
}

export default function Home() {
  const [config, setConfig] = useState<Config | null>(null);
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState<Mode>("scripted");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");
  const [tab, setTab] = useState<FindingState | "coverage">("confirmed");
  const [demo, setDemo] = useState(false);
  const reportRef = useRef<HTMLElement>(null);
  const active = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    request<Config>("/api/config")
      .then(setConfig)
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!active || !job?.id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await request<Job>(
          `/api/jobs/${encodeURIComponent(job!.id)}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setJob(next);
        setConnectionError("");
        if (next.status === "completed" || next.status === "failed") return;
      } catch (err) {
        if (controller.signal.aborted) return;
        setConnectionError(
          `${(err as Error).message} Progress will update when the connection returns.`,
        );
      }
      timer = setTimeout(poll, 1500);
    }
    timer = setTimeout(poll, 750);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [active, job?.id]);

  async function start(target: string, inject = false, scanMode: Mode = mode) {
    setError("");
    setConnectionError("");
    setBusy(true);
    try {
      const next = await request<Job>("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: target,
          mode: inject ? "scripted" : scanMode,
          demo_fault_injection: inject,
        }),
      });
      setJob(next);
      setDemo(inject);
      setTab("confirmed");
      setTimeout(
        () =>
          reportRef.current?.scrollIntoView({
            behavior: "smooth",
            block: "start",
          }),
        100,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void start(url.trim());
  }
  function download() {
    if (!job?.report) return;
    const file = new Blob([JSON.stringify(job.report, null, 2)], {
      type: "application/json",
    });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(file);
    link.download = `arsune-${job.id}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  const findings = job?.report?.findings || job?.findings || [];
  const pages =
    job?.report?.inspected_pages || job?.report?.selected_pages || [];
  const count = (state: FindingState) =>
    findings.filter((item) => item.status === state).length;

  return (
    <>
      <header className="site-header">
        <a href="/" className="brand" aria-label="Arsune home">
          <span className="brand-mark" aria-hidden="true">
            a
          </span>
          arsune<span className="brand-dot">.</span>
        </a>
        <span className="header-note">
          <span className="status-dot" />
          Local website checker
        </span>
      </header>
      <main>
        <section className="intro">
          <div className="intro-copy">
            <p className="eyebrow">
              <span className="short-line" /> CHECK. VERIFY. UNDERSTAND.
            </p>
            <h1>
              A second look.
              <br />
              <span>A clearer website.</span>
            </h1>
            <p className="lead">
              Find broken links, mobile layout faults, and conflicting
              information. Each reported fault gets a separate check, with
              evidence you can inspect.
            </p>
            <div className="scope-note">
              <span aria-hidden="true">↳</span> Up to five pages. Desktop and
              mobile. Your site stays unchanged.
            </div>
          </div>
          <div className="scan-panel">
            <div className="panel-title">
              <span className="step-number">01</span>
              <h2>Start a website check</h2>
            </div>
            <form onSubmit={submit}>
              <label htmlFor="website">Website URL</label>
              <input
                id="website"
                type="url"
                placeholder="https://your-website.com"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                required
                disabled={busy || active}
                autoComplete="url"
              />
              <fieldset disabled={busy || active}>
                <legend>Check mode</legend>
                <div className="mode-options">
                  <label className={mode === "scripted" ? "selected" : ""}>
                    <input
                      type="radio"
                      name="mode"
                      value="scripted"
                      checked={mode === "scripted"}
                      onChange={() => setMode("scripted")}
                    />
                    <span>
                      <strong>Scripted</strong>
                      <small>Repeatable browser checks</small>
                    </span>
                  </label>
                  <label className={mode === "model" ? "selected" : ""}>
                    <input
                      type="radio"
                      name="mode"
                      value="model"
                      disabled={!config?.modes.includes("model")}
                      checked={mode === "model"}
                      onChange={() => setMode("model")}
                    />
                    <span>
                      <strong>Model</strong>
                      <small>
                        {config?.modes.includes("model")
                          ? "Model suggests findings"
                          : "Not configured"}
                      </small>
                    </span>
                  </label>
                </div>
              </fieldset>
              <button
                className="primary"
                type="submit"
                disabled={busy || active}
              >
                {busy
                  ? "Starting check…"
                  : active
                    ? "Check in progress…"
                    : "Check website"}
                <span aria-hidden="true">↗</span>
              </button>
            </form>
            <div className="demo-block">
              <p>Try the controlled demo</p>
              <div className="demo-actions">
                <button
                  disabled={!config || busy || active}
                  onClick={() => {
                    setUrl(config!.demo_urls.faulty);
                    void start(config!.demo_urls.faulty, true);
                  }}
                >
                  Site with faults <span aria-hidden="true">↗</span>
                </button>
                <button
                  disabled={!config || busy || active}
                  onClick={() => {
                    setUrl(config!.demo_urls.corrected);
                    void start(config!.demo_urls.corrected, false, "scripted");
                  }}
                >
                  Corrected site <span aria-hidden="true">↗</span>
                </button>
              </div>
              <small>
                The faulty demo adds one labeled false claim to test the
                checker. Both demo scans use scripted mode.
              </small>
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
          </div>
        </section>
        <section className="method" aria-label="How a check works">
          <div>
            <span>01 / OBSERVE</span>
            <p>
              Three specialists inspect navigation, mobile layout, and page
              consistency.
            </p>
          </div>
          <div>
            <span>02 / REPEAT</span>
            <p>
              A separate checker repeats each supported check against the
              website.
            </p>
          </div>
          <div>
            <span>03 / REVIEW</span>
            <p>
              See confirmed faults, rejected claims, and checks that need more
              evidence.
            </p>
          </div>
        </section>
        {job && (
          <section
            className="workspace"
            ref={reportRef}
            aria-label="Website check results"
          >
            <div className="section-top">
              <div>
                <p className="eyebrow">YOUR WEBSITE CHECK</p>
                <h2>
                  {job.status === "completed"
                    ? "Evidence, before advice."
                    : job.status === "failed"
                      ? "The check could not finish."
                      : "A closer look is in progress."}
                </h2>
                <p className="url">{job.url}</p>
              </div>
              <span className={`badge job-${job.status}`}>{job.status}</span>
            </div>
            <div className="job-meta">
              <span>
                {job.mode === "scripted"
                  ? "Scripted browser checks"
                  : "Model-assisted checks"}
              </span>
              <span>Started {new Date(job.created_at).toLocaleString()}</span>
              <span>Job {job.id.slice(0, 8)}</span>
            </div>
            {demo && (
              <p className="notice">
                Controlled demo: one false claim was added to test the
                independent checker.
              </p>
            )}
            {connectionError && (
              <p className="error" role="alert">
                {connectionError}
              </p>
            )}
            {job.error && (
              <p className="error" role="alert">
                {job.error}
              </p>
            )}
            <div className="activity">
              <div className="activity-heading">
                <h3>Check activity</h3>
                <p role="status" aria-live="polite">
                  {active && <span className="spinner" />}
                  {displayMessage(job.progress?.message || job.status)}
                </p>
              </div>
              <div className="roles">
                {roles.map((role) => {
                  const event = [...(job.events || [])]
                    .reverse()
                    .find((item) => item.role.toLowerCase() === role);
                  return (
                    <div key={role} className={event ? "role started" : "role"}>
                      <span className="role-icon" aria-hidden="true">
                        {role === "navigation"
                          ? "↗"
                          : role === "mobile"
                            ? "▯"
                            : role === "consistency"
                              ? "≋"
                              : "✓"}
                      </span>
                      <h4>{names[role]}</h4>
                      <p>
                        {event
                          ? displayMessage(event.message)
                          : active
                            ? "Waiting for observations"
                            : "No activity recorded"}
                      </p>
                    </div>
                  );
                })}
              </div>
              {job.events?.length > 0 && (
                <details className="activity-log">
                  <summary>View activity log ({job.events.length})</summary>
                  <ol>
                    {job.events.map((event) => (
                      <li key={event.id}>
                        <time>
                          {new Date(event.created_at).toLocaleTimeString()}
                        </time>
                        <strong>{names[event.role] || event.role}</strong>
                        <span>{displayMessage(event.message)}</span>
                      </li>
                    ))}
                  </ol>
                </details>
              )}
            </div>
            {job.report && (
              <div className="report">
                <div className="report-heading">
                  <div>
                    <p className="eyebrow">REPORT</p>
                    <h3>
                      {count("confirmed")} confirmed{" "}
                      {count("confirmed") === 1 ? "fault" : "faults"}
                    </h3>
                  </div>
                  <button className="text-button" onClick={download}>
                    Download report ↓
                  </button>
                </div>
                <p className="report-scope">
                  Results apply to the pages and checks recorded below. They do
                  not establish that the whole website is fault-free.
                </p>
                <div
                  className="tabs"
                  role="tablist"
                  aria-label="Report sections"
                >
                  {(
                    ["confirmed", "rejected", "unverified", "coverage"] as const
                  ).map((state) => (
                    <button
                      id={`tab-${state}`}
                      key={state}
                      role="tab"
                      aria-selected={tab === state}
                      aria-controls="report-panel"
                      tabIndex={tab === state ? 0 : -1}
                      onClick={() => setTab(state)}
                      onKeyDown={(event) => {
                        if (
                          event.key === "ArrowRight" ||
                          event.key === "ArrowLeft"
                        ) {
                          const states = [
                            "confirmed",
                            "rejected",
                            "unverified",
                            "coverage",
                          ] as const;
                          const next =
                            states[
                              (states.indexOf(state) +
                                (event.key === "ArrowRight" ? 1 : 3)) %
                                4
                            ];
                          setTab(next);
                          document.getElementById(`tab-${next}`)?.focus();
                        }
                      }}
                    >
                      {state === "coverage"
                        ? "Coverage"
                        : state[0].toUpperCase() + state.slice(1)}
                      {state !== "coverage" && <span>{count(state)}</span>}
                    </button>
                  ))}
                </div>
                <div
                  id="report-panel"
                  role="tabpanel"
                  aria-labelledby={`tab-${tab}`}
                  tabIndex={0}
                >
                  {tab === "coverage" ? (
                    <CoveragePanel
                      report={job.report}
                      pages={pages}
                      mode={job.mode}
                    />
                  ) : findings.filter((item) => item.status === tab).length ? (
                    findings
                      .filter((item) => item.status === tab)
                      .map((item) => (
                        <FindingCard
                          key={item.id}
                          finding={item}
                          jobId={job.id}
                        />
                      ))
                  ) : (
                    <div className="empty-state">
                      <span aria-hidden="true">
                        {tab === "confirmed" ? "✓" : "—"}
                      </span>
                      <h3>No {tab} findings in this report</h3>
                      <p>
                        {tab === "confirmed"
                          ? "Review coverage for the pages and checks that were completed."
                          : tab === "rejected"
                            ? "No candidate claim received this result."
                            : "Review coverage for any incomplete checks."}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </section>
        )}
      </main>
      <footer>
        <a href="/" className="brand">
          arsune.
        </a>
        <p>Useful checks. Visible evidence. Clear next steps.</p>
        <span>Local prototype · Five-page limit</span>
      </footer>
    </>
  );
}
