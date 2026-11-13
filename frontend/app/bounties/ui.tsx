"use client";

import { useState } from "react";
import "./bounties.css";

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2500);
  }
  return (
    <button
      type="button"
      className="bo-copy"
      onClick={copy}
      aria-label={`Copy ${label}`}
    >
      {state === "copied"
        ? "Copied"
        : state === "failed"
          ? "Select and copy"
          : "Copy"}
    </button>
  );
}

// The one simulation label of a page, in its header.
export function SimTag({ show }: { show: boolean }) {
  return show ? (
    <span className="sw-sim" data-testid="sim-label">
      Simulation · no real money
    </span>
  ) : null;
}

/** The small word of a disclosure: "Show" while it is closed, "Hide" while it is open. */
export function ShowHide() {
  return (
    <>
      <span className="bo-show">Show</span>
      <span className="bo-hide">Hide</span>
    </>
  );
}

export function Code({
  text,
  label,
  testId,
}: {
  text: string;
  label: string;
  testId?: string;
}) {
  return (
    <div className="bo-code">
      <code data-testid={testId}>{text}</code>
      <CopyButton text={text} label={label} />
    </div>
  );
}

export function Message({
  kind,
  text,
  testId,
}: {
  kind: "error" | "ok" | "info";
  text: string | null;
  testId?: string;
}) {
  if (!text) return null;
  return (
    <p
      className={`bo-msg bo-msg-${kind}`}
      role={kind === "error" ? "alert" : "status"}
      data-testid={testId}
    >
      {text}
    </p>
  );
}
