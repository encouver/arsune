"use client";

import { useEffect, useRef, useState } from "react";
import {
  SECURITY_TYPES,
  claimGroup,
  claimPage,
  titler,
  type ClaimText,
} from "./claim-text";

type BugClaim = ClaimText & {
  id: string;
  created_at?: string;
  fingerprint?: string | null;
  evidence_url?: string | null;
};

type Row = {
  id: string;
  title: string;
  fullPath?: string;
  security: boolean;
  page: string;
  href: string;
};

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

function leakTitle(c: BugClaim, fallback: string) {
  const steps = Array.isArray(c.details?.steps) ? c.details.steps : [];
  const step = steps[steps.length - 1] as Record<string, unknown> | undefined;
  const path = typeof step?.path === "string" ? step.path : "";
  if (!path) return { title: fallback };
  const method = typeof step?.method === "string" ? step.method : "";
  const bare = path.split("?")[0];
  return {
    title: `Leak: ${method} ${bare}`.replace("  ", " "),
    fullPath: path,
  };
}

function listText(site: string, security: Row[], other: Row[]) {
  const line = (r: Row) => `- ${r.title} — ${r.page}`;
  const lines = [
    `Bugs found on ${site} by Arsune (${security.length + other.length} proven)`,
  ];
  if (security.length) lines.push("", "Security leaks", ...security.map(line));
  if (other.length) lines.push("", "Other bugs", ...other.map(line));
  return lines.join("\n");
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
  const rows: Row[] = proven.map((c) => {
    const security = SECURITY_TYPES.includes(c.claim_type);
    return {
      id: c.id,
      security,
      ...(c.claim_type === "access_leak"
        ? leakTitle(c, titleOf(c))
        : { title: titleOf(c) }),
      page: claimPage(c),
      href: c.evidence_url || evidenceHref,
    };
  });
  const security = rows.filter((r) => r.security);
  const other = rows.filter((r) => !r.security);
  const text = listText(site, security, other);

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

  function renderRows(list: Row[]) {
    return (
      <ul className="bf-list">
        {list.map((r) => (
          <li
            key={r.id}
            className={fresh.has(r.id) ? "bf-new" : undefined}
            data-testid={r.security ? "security-row" : "bug-row"}
          >
            {r.security ? (
              <span className="bf-tag">Security</span>
            ) : (
              <span className="bf-mark" aria-hidden="true">
                ✓
              </span>
            )}
            <span className="bf-title" title={r.fullPath}>
              {r.title}
            </span>
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
    );
  }

  return (
    <section
      className="rv-card bf"
      aria-label="Bugs found"
      data-testid="bugs-found"
    >
      <div className="bf-head">
        <h2>
          {rows.length === 0
            ? "Bugs found · 0 proven"
            : `Found: ${plural(security.length, "security leak", "security leaks")} · ${other.length} other ${other.length === 1 ? "bug" : "bugs"}`}
        </h2>
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
        <>
          {security.length > 0 && (
            <>
              <h3 className="bf-sub">Security leaks · {security.length}</h3>
              {renderRows(security)}
            </>
          )}
          {other.length > 0 &&
            (security.length > 0 ? (
              <details className="bf-other">
                <summary>Other bugs · {other.length}</summary>
                {renderRows(other)}
              </details>
            ) : (
              <>
                <h3 className="bf-sub">Other bugs · {other.length}</h3>
                {renderRows(other)}
              </>
            ))}
        </>
      )}
    </section>
  );
}
