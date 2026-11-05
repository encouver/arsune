"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import RecentChecks from "../recent-checks";

type Ref = {
  filename: string;
  sha256: string;
  line: number | null;
  record_id: string;
  record?: number;
  timestamp: string;
  excerpt: string;
};
type Finding = {
  id: string;
  status: string;
  explanation: string;
  checker_reason?: string;
  evidence: Ref[];
  evidence_records_total: number;
  evidence_records_omitted: number;
  evidence_limit: number;
};
type Report = {
  service: string;
  description: string;
  start: string;
  end: string;
  timeline: {
    id: string;
    timestamp: string;
    kind: string;
    message?: string;
    revision?: string;
    evidence: Ref;
  }[];
  findings: Finding[];
  missing_evidence: string[];
  next_checks: string[];
  coverage: {
    accepted_events: number;
    selected_events: number;
    excluded_events: number;
    accepted_deployments: number;
    selected_deployments: number;
    excluded_deployments: number;
    timeline_total: number;
    timeline_limit: number;
    error_group_limit: number;
    error_groups_total: number;
    error_groups_omitted: number;
  };
};
type Job = {
  id: string;
  kind: string;
  status: string;
  error?: string;
  progress?: { stage: string; message: string };
  report?: Report;
  events?: { id: string; role: string; message: string }[];
};
type Source = {
  filename: string;
  sha256: string;
  record_id: string;
  line: number | null;
  record_index?: number;
  raw: string;
};
const maxBytes = 10 * 1024 * 1024;
const groups = [
  ["observed_fact", "Observed facts"],
  ["supported_explanation", "Supported explanations"],
  ["rejected_explanation", "Rejected explanations"],
  ["could_not_verify", "Could not verify"],
];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, cache: "no-store" });
  const body = await response.json().catch(() => {
    throw new Error("The service returned an unreadable response. Try again.");
  });
  if (!response.ok)
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : "The service could not accept this request.",
    );
  return body as T;
}

function Evidence({ jobId, source }: { jobId: string; source: Ref }) {
  const [record, setRecord] = useState<Source | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = `/api/incidents/${encodeURIComponent(jobId)}/evidence/${encodeURIComponent(source.filename)}?record_id=${encodeURIComponent(source.record_id)}`;
  async function load() {
    setBusy(true);
    setError("");
    try {
      const result = await request<Source>(path);
      if (result.sha256 !== source.sha256)
        throw new Error(
          "The source hash does not match this report. The record cannot be shown as evidence.",
        );
      setRecord(result);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="incident-evidence">
      <button
        type="button"
        className="text-button"
        disabled={busy}
        onClick={() => void load()}
      >
        {busy
          ? "Loading source…"
          : `View source: ${source.filename} · ${source.record_id} · ${source.line ? `line ${source.line}` : `record ${source.record || source.record_id}`}`}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {record && (
        <div className="source-record">
          <p>
            {record.filename} · Record {record.record_id} ·{" "}
            {record.line
              ? `Line ${record.line}`
              : `Record index ${record.record_index}`}
          </p>
          <p className="source-hash">SHA-256: {record.sha256}</p>
          <pre aria-label="Exact source record">{record.raw}</pre>
        </div>
      )}
    </div>
  );
}

export default function Incidents() {
  const resultRef = useRef<HTMLElement>(null);
  const [description, setDescription] = useState("");
  const [service, setService] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [events, setEvents] = useState("");
  const [deployments, setDeployments] = useState("");
  const [eventsName, setEventsName] = useState("");
  const [deploymentsName, setDeploymentsName] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [jobId, setJobId] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    if (job?.id) resultRef.current?.scrollIntoView({ block: "start" });
  }, [job?.id]);

  useEffect(() => {
    setJobId(new URLSearchParams(window.location.search).get("job") || "");
  }, []);
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await request<Job>(
          `/api/jobs/${encodeURIComponent(jobId)}`,
        );
        if (stopped) return;
        if (result.kind !== "incident")
          throw new Error(
            "This saved job is a website check. Open it from Recent checks on the website page.",
          );
        setJob(result);
        setPollError("");
        if (!["completed", "failed"].includes(result.status))
          timer = setTimeout(() => void poll(), 1000);
      } catch (cause) {
        if (!stopped) setPollError((cause as Error).message);
      }
    }
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [jobId, retry]);

  async function readFile(
    file: File | undefined,
    kind: "events" | "deployments",
  ) {
    if (!file) return;
    if (kind === "events") {
      setEvents("");
      setEventsName(file.name);
    } else {
      setDeployments("");
      setDeploymentsName(file.name);
    }
    setError("");
    if (file.size > maxBytes) {
      setError("Each file must be no larger than 10 MB.");
      return;
    }
    try {
      const text = new TextDecoder("utf-8", {
        fatal: true,
        ignoreBOM: true,
      }).decode(await file.arrayBuffer());
      if (kind === "events") {
        setEvents(text);
        setEventsName(file.name);
      } else {
        setDeployments(text);
        setDeploymentsName(file.name);
      }
    } catch {
      setError(
        "The file could not be read as valid UTF-8 text. Select a UTF-8 file.",
      );
    }
  }

  async function example() {
    setBusy(true);
    setError("");
    try {
      const responses = await Promise.all([
        fetch("/incidents/events.ndjson"),
        fetch("/incidents/deployments.json"),
      ]);
      if (responses.some((response) => !response.ok))
        throw new Error("The example files could not load. Try again.");
      const [eventText, deploymentText] = await Promise.all(
        responses.map((response) => response.text()),
      );
      setEvents(eventText);
      setDeployments(deploymentText);
      setEventsName("Synthetic events.ndjson");
      setDeploymentsName("Synthetic deployments.json");
      setDescription("Checkout requests fail after deployment v2.");
      setService("checkout");
      setStart("2026-11-05T09:55:00Z");
      setEnd("2026-11-05T10:15:00Z");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      if (!events.trim() || !deployments.trim())
        throw new Error(
          "Select both events.ndjson and deployments.json, or load the synthetic example.",
        );
      if (new Blob([events, deployments]).size > maxBytes)
        throw new Error("The two files together must be no larger than 10 MB.");
      if (
        !/(Z|[+-]\d{2}:\d{2})$/i.test(start) ||
        !/(Z|[+-]\d{2}:\d{2})$/i.test(end)
      )
        throw new Error(
          "Each time must include Z or an explicit offset such as +00:00.",
        );
      const duration = Date.parse(end) - Date.parse(start);
      if (!Number.isFinite(duration) || duration <= 0 || duration > 86400000)
        throw new Error(
          "Use a valid time window longer than zero and no longer than 24 hours.",
        );
      setBusy(true);
      const result = await request<Job>("/api/incidents", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description,
          service,
          start,
          end,
          events_text: events,
          deployments_text: deployments,
        }),
      });
      setJob(null);
      setJobId(result.id);
      setPollError("");
      window.history.replaceState(
        {},
        "",
        `/incidents?job=${encodeURIComponent(result.id)}`,
      );
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="site-header">
        <a href="/" className="brand">
          arsune<span className="brand-dot">.</span>
        </a>
        <nav className="task-nav" aria-label="Tasks">
          <a href="/">Website checks</a>
          <a href="/incidents" aria-current="page">
            Incident investigation
          </a>
        </nav>
      </header>
      <main>
        <section className="intro incident-intro">
          <div>
            <p className="eyebrow">LOCAL INCIDENT INVESTIGATION</p>
            <h1>
              Read the records.
              <br />
              <span>Check the explanation.</span>
            </h1>
            <p className="lead">
              Upload local logs and deployment records. Get a timeline and
              possible explanations with source evidence. No credentials or
              service connection are required.
            </p>
            <p className="notice">
              Logs can support a possible cause. They cannot prove a cause or
              show that a service was repaired.
            </p>
            <details className="incident-format">
              <summary>Supported file formats and limits</summary>
              <p>
                Maximum: 10 MB total, 5,000 events, 20 deployments, and a
                24-hour window. All timestamps need Z or an explicit UTC offset.
                The service checks every record.
              </p>
              <p>
                <strong>events.ndjson:</strong> one JSON object per line.
                Required fields: id, timestamp, service, level, message.
                Optional: request_id, error_code, revision, column.
              </p>
              <p>
                <strong>deployments.json:</strong> a JSON array. Required
                fields: id, timestamp, service, revision, migration_status
                (failed, completed, or not_required). Optional:
                migration_column.
              </p>
              <p>IDs must be unique. File text is treated as data.</p>
              <p>
                <a download href="/incidents/events.ndjson">
                  Download example events
                </a>{" "}
                ·{" "}
                <a download href="/incidents/deployments.json">
                  Download example deployments
                </a>
              </p>
            </details>
          </div>
          <form
            className="scan-panel incident-form"
            onSubmit={(event) => void submit(event)}
          >
            <h2>Investigate an incident</h2>
            <label htmlFor="description">Incident description</label>
            <textarea
              id="description"
              required
              maxLength={2000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
            <label htmlFor="service">Service</label>
            <input
              id="service"
              required
              maxLength={200}
              value={service}
              onChange={(event) => setService(event.target.value)}
            />
            <label htmlFor="start">Start time with timezone</label>
            <input
              id="start"
              required
              placeholder="2026-11-05T09:55:00Z"
              value={start}
              onChange={(event) => setStart(event.target.value)}
            />
            <label htmlFor="end">End time with timezone</label>
            <input
              id="end"
              required
              placeholder="2026-11-05T10:15:00+00:00"
              value={end}
              onChange={(event) => setEnd(event.target.value)}
            />
            <p className="field-help">
              Use ISO 8601 with Z for UTC, or an offset such as -06:00.
            </p>
            <label htmlFor="events-file">events.ndjson</label>
            <input
              id="events-file"
              type="file"
              accept=".ndjson,.jsonl,application/x-ndjson"
              onChange={(event) =>
                void readFile(event.target.files?.[0], "events")
              }
            />
            <p className="field-help">{eventsName || "No events selected"}</p>
            <label htmlFor="deployments-file">deployments.json</label>
            <input
              id="deployments-file"
              type="file"
              accept=".json,application/json"
              onChange={(event) =>
                void readFile(event.target.files?.[0], "deployments")
              }
            />
            <p className="field-help">
              {deploymentsName || "No deployments selected"}
            </p>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button className="primary" disabled={busy} type="submit">
              {busy ? "Please wait…" : "Investigate incident"}
              <span aria-hidden="true">→</span>
            </button>
            <div className="demo-block">
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => void example()}
              >
                Load synthetic example
              </button>
              <p className="field-help">
                Loads safe sample records and the time window. Then select
                Investigate incident.
              </p>
            </div>
          </form>
        </section>
        {(jobId || pollError) && (
          <section
            className="workspace incident-report"
            ref={resultRef}
            aria-label="Incident result"
          >
            {pollError && (
              <div role="alert" className="error">
                {pollError}{" "}
                <button type="button" onClick={() => setRetry(retry + 1)}>
                  Try loading again
                </button>
              </div>
            )}
            {!job && !pollError && <p role="status">Loading saved incident…</p>}
            {job && (
              <>
                <div className="section-top">
                  <h2>Incident investigation</h2>
                  <span className={`badge job-${job.status}`}>
                    {job.status}
                  </span>
                </div>
                <p className="job-meta">Job {job.id}</p>
                {job.error && (
                  <p className="error" role="alert">
                    {job.error}
                  </p>
                )}
                <div className="activity">
                  <div className="activity-heading">
                    <h3>Investigation progress</h3>
                    <p role="status">
                      {job.progress?.message ||
                        job.progress?.stage ||
                        job.status}
                    </p>
                  </div>
                  <ol>
                    {job.events?.map((item) => (
                      <li key={item.id}>
                        <strong>{item.role}</strong>: {item.message}
                      </li>
                    ))}
                  </ol>
                </div>
                {job.report && (
                  <>
                    <h2>Incident report</h2>
                    <p>{job.report.description}</p>
                    <p>
                      {job.report.service} · {job.report.start} to{" "}
                      {job.report.end}
                    </p>
                    <p className="notice">
                      This report uses supplied records only. Possible
                      explanations need further checks. No cause was reproduced
                      and no repair was performed.
                    </p>
                    <section className="coverage">
                      <h3>Timeline</h3>
                      <p>
                        {job.report.coverage.selected_events} of{" "}
                        {job.report.coverage.accepted_events} events and{" "}
                        {job.report.coverage.selected_deployments} of{" "}
                        {job.report.coverage.accepted_deployments} deployments
                        match the service and time window.{" "}
                        {job.report.coverage.excluded_events} events and{" "}
                        {job.report.coverage.excluded_deployments} deployments
                        were excluded.
                      </p>
                      <p>
                        Showing {job.report.timeline.length} of{" "}
                        {job.report.coverage.timeline_total} timeline records.
                        The report groups at most{" "}
                        {job.report.coverage.error_group_limit} exact error
                        signatures. {job.report.coverage.error_groups_omitted}{" "}
                        of {job.report.coverage.error_groups_total} error groups
                        were omitted.
                      </p>
                      <ol>
                        {job.report.timeline.map((item, index) => (
                          <li key={`${item.kind}-${item.id}-${index}`}>
                            <time>{item.timestamp}</time> · {item.kind} ·{" "}
                            {item.id}
                            <p>{item.message || item.revision}</p>
                            {item.evidence && (
                              <Evidence jobId={job.id} source={item.evidence} />
                            )}
                          </li>
                        ))}
                      </ol>
                    </section>
                    {groups.map(([status, title]) => (
                      <section className="incident-group" key={status}>
                        <h3>{title}</h3>
                        {job
                          .report!.findings.filter(
                            (item) => item.status === status,
                          )
                          .map((item) => (
                            <article className="finding" key={item.id}>
                              <h4>{item.id}</h4>
                              <p>{item.explanation}</p>
                              {item.checker_reason && (
                                <p className="check-reason">
                                  Source check: {item.checker_reason}
                                </p>
                              )}
                              <p>
                                Showing {item.evidence.length} of{" "}
                                {item.evidence_records_total} source records.{" "}
                                {item.evidence_records_omitted} records were
                                omitted (limit: {item.evidence_limit}).
                              </p>
                              {item.evidence.map((source, index) => (
                                <Evidence
                                  key={`${source.filename}-${source.record_id}-${index}`}
                                  jobId={job.id}
                                  source={source}
                                />
                              ))}
                            </article>
                          ))}
                        {!job.report!.findings.some(
                          (item) => item.status === status,
                        ) && <p>No records in this group.</p>}
                      </section>
                    ))}
                    <section className="coverage">
                      <h3>Missing evidence</h3>
                      {job.report.missing_evidence.length ? (
                        <ul>
                          {job.report.missing_evidence.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      ) : (
                        <p>No additional gaps were recorded.</p>
                      )}
                      <h3>Next checks</h3>
                      <ul>
                        {job.report.next_checks.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </section>
                  </>
                )}
              </>
            )}
          </section>
        )}
        <RecentChecks updated={`${job?.id}:${job?.status}`} />
      </main>
    </>
  );
}
