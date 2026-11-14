"use client";

import { useEffect, useRef, useState } from "react";
import { claimGroup, claimPage, titler, type ClaimText } from "./claim-text";

type BugClaim = ClaimText & {
  id: string;
  created_at?: string;
  fingerprint?: string | null;
  evidence_url?: string | null;
};

function listText(site: string, rows: { title: string; page: string }[]) {
  return [
    `Bugs found on ${site} by Arsune (${rows.length} proven)`,
    ...rows.map((r) => `- ${r.title} — ${r.page}`),
  ].join("\n");
}

/** The proven bugs of a hunt, newest first, with copy and download. */
export function BugsFound({
  claims,
  site,
  bountyId,
  finished,
  evidenceHref,
}: {
  claims: BugClaim[];
  site: string;
  bountyId: string;
  finished: boolean;
  evidenceHref: string;
}) {
  const titleOf = titler(claims);
  const proven = claims
    .filter((c) => claimGroup(c) === "proven")
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const rows = proven.map((c) => ({
    id: c.id,
    title: titleOf(c),
    page: claimPage(c),
    href: c.evidence_url || evidenceHref,
  }));
  const text = listText(site, rows);

  // Rows that were not in the previous render get a short highlight; the first render has none.
  const seen = useRef<Set<string> | null>(null);
  const fresh = seen.current
    ? new Set(rows.filter((r) => !seen.current?.has(r.id)).map((r) => r.id))
    : new Set<string>();
  useEffect(() => {
    seen.current = new Set(rows.map((r) => r.id));
  });

  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      // Clipboard can be blocked; Download still works.
    }
  }

  function download() {
    const url = URL.createObjectURL(
      new Blob([text + "\n"], { type: "text/markdown" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `arsune-bugs-${bountyId.slice(0, 8)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <section
      className="rv-card bf"
      aria-label="Bugs found"
      data-testid="bugs-found"
    >
      <div className="bf-head">
        <h2>Bugs found · {rows.length} proven</h2>
        {rows.length > 0 && (
          <div className="bf-actions">
            <button type="button" onClick={copy}>
              {copied ? "Copied" : "Copy as list"}
            </button>
            <button type="button" onClick={download}>
              Download
            </button>
          </div>
        )}
      </div>
      {rows.length === 0 ? (
        <p className="bf-empty">
          {finished
            ? "This hunt found no proven bugs."
            : "No bugs proven yet. Agents are searching."}
        </p>
      ) : (
        <ul className="bf-list">
          {rows.map((r) => (
            <li
              key={r.id}
              className={fresh.has(r.id) ? "bf-new" : undefined}
              data-testid="bug-row"
            >
              <span className="bf-mark" aria-hidden="true">
                ✓
              </span>
              <span className="bf-title">{r.title}</span>
              <code className="bf-page" title={r.page}>
                {r.page}
              </code>
              <a
                href={r.href}
                {...(r.href.startsWith("#")
                  ? {}
                  : { target: "_blank", rel: "noreferrer" })}
              >
                Evidence →
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
