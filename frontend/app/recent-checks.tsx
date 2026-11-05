"use client";

import { useEffect, useState } from "react";

type Summary = {
  id: string;
  url: string;
  mode: string;
  status: string;
  baseline_job_id: string | null;
  created_at: string;
};
type HistoryPage = { items: Summary[]; next_cursor: string | null };

export default function RecentChecks({ updated }: { updated?: string }) {
  const [history, setHistory] = useState<HistoryPage>({
    items: [],
    next_cursor: null,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  async function load(cursor?: string, signal?: AbortSignal) {
    setBusy(true);
    setError("");
    try {
      const query = new URLSearchParams({ limit: "10" });
      if (cursor) query.set("cursor", cursor);
      const response = await fetch(`/api/jobs?${query}`, {
        cache: "no-store",
        signal,
      });
      if (!response.ok)
        throw new Error(
          "Saved checks could not load. Select Refresh to try again.",
        );
      const result = (await response.json()) as HistoryPage;
      if (!Array.isArray(result.items))
        throw new Error(
          "Saved checks could not load. Select Refresh to try again.",
        );
      if (!signal?.aborted) setHistory(result);
    } catch (cause) {
      if (!signal?.aborted) setError((cause as Error).message);
    } finally {
      if (!signal?.aborted) setBusy(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void load(undefined, controller.signal);
    return () => controller.abort();
  }, [updated, refresh]);

  return (
    <section className="recent-checks" aria-label="Recent checks">
      <div className="report-heading">
        <h2>Recent checks</h2>
        <button
          className="text-button"
          disabled={busy}
          onClick={() => setRefresh(refresh + 1)}
        >
          Refresh
        </button>
      </div>
      <p>
        Saved checks on this local service. Open a check to view its report or
        progress.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {busy && <p role="status">Loading saved checks…</p>}
      {!busy && !error && !history.items.length && <p>No saved checks yet.</p>}
      <ul>
        {history.items.map((item) => (
          <li key={item.id}>
            <a className="url" href={`/?job=${encodeURIComponent(item.id)}`}>
              {item.url}
            </a>
            <div className="history-meta">
              <span className={`badge job-${item.status}`}>{item.status}</span>
              <time dateTime={item.created_at}>
                {new Date(item.created_at).toLocaleString()}
              </time>
              <span>{item.mode === "scripted" ? "Scripted" : "Model"}</span>
              <span>Job {item.id.slice(0, 8)}</span>
              {item.baseline_job_id && (
                <a href={`/?job=${encodeURIComponent(item.baseline_job_id)}`}>
                  Baseline report
                </a>
              )}
            </div>
          </li>
        ))}
      </ul>
      {history.next_cursor && (
        <button
          className="text-button"
          disabled={busy}
          onClick={() => void load(history.next_cursor!)}
        >
          Older checks
        </button>
      )}
    </section>
  );
}
