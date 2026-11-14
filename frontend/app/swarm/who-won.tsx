"use client";

import { useEffect, useLayoutEffect, useRef, useState, type JSX } from "react";
import {
  agentLabel,
  agentRows,
  isWorking,
  net,
  resultOf,
  signedUsdc,
  type Result,
} from "./race-model";
import type { Agent } from "./types";

export type WhoWonProps = {
  agents: Agent[];
  // Agents rebuilt from Arsune's records (no race state): no rings, no strategy.
  fallback: boolean;
  running: boolean;
  // The account of the picked agent.
  picked: string | null;
  onPick: (a: Agent) => void;
};

const ROWS: [Result, string, string][] = [
  ["earned", "Earned", "proved bugs, got paid"],
  ["lost", "Lost money", "fake reports cost more than it earned"],
  ["broke", "Went broke", "lost all its deposit money, stopped"],
  ["none", "Nothing yet", "no proven bug so far"],
];

function rowTotal(result: Result, agents: Agent[]): string {
  if (result === "none") return "deposits back";
  return `${signedUsdc(agents.reduce((sum, a) => sum + net(a), 0))} USDC`;
}

// The tooltip opens toward the middle of the screen, so it never makes the page wider.
type Tip = {
  account: string;
  result: Result;
  side: "left" | "right";
};

function tipFor(a: Agent, el: HTMLElement): Tip {
  const box = el.getBoundingClientRect();
  const side = box.left + box.width / 2 > window.innerWidth / 2;
  return {
    account: a.account,
    result: resultOf(a),
    side: side ? "right" : "left",
  };
}

// A long label can still pass the screen edge: move it back inside before it paints.
function TipText({ text, side }: { text: string; side: Tip["side"] }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [shift, setShift] = useState(0);
  useLayoutEffect(() => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const room = document.documentElement.clientWidth - 8;
    if (box.left < 8) setShift(8 - box.left);
    else if (box.right > room) setShift(room - box.right);
  }, []);
  return (
    <span
      ref={ref}
      className="rv-tip"
      data-side={side}
      data-testid="agent-tip"
      role="tooltip"
      style={{ translate: `${shift}px 0` }}
    >
      {text}
    </span>
  );
}

export default function WhoWon({
  agents,
  fallback,
  running,
  picked,
  onPick,
}: WhoWonProps): JSX.Element | null {
  const [tip, setTip] = useState<Tip | null>(null);
  const marks = useRef(new Map<string, HTMLButtonElement>());
  const focused = useRef<string | null>(null);
  // An agent that changes result moves to another row: its old button unmounts.
  useEffect(() => {
    const now = agents.find((a) => a.account === tip?.account);
    if (tip && (!now || resultOf(now) !== tip.result)) setTip(null);
    if (!focused.current || document.activeElement !== document.body) return;
    marks.current.get(focused.current)?.focus();
  });
  if (agents.length === 0) return null;
  const rows = agentRows(agents);
  const rings = running && !fallback;
  return (
    <section
      className="rv-card rv-who"
      data-testid="who-won"
      aria-label="Who won"
    >
      <div className="rv-who-head">
        <h2>Who won</h2>
        <ul className="rv-key">
          <li>
            <i className="rv-mark-key" /> hunter
          </li>
          <li>
            <i className="rv-mark-key rv-mark-key-scout" /> scout
          </li>
          {rings && (
            <li>
              <i className="rv-mark-key rv-mark-key-ring" /> working now
            </li>
          )}
          <li>Point at a dot to see the agent</li>
        </ul>
      </div>
      {ROWS.map(([result, name, note]) => (
        <div
          key={result}
          className="rv-who-row"
          data-testid="who-row"
          data-result={result}
        >
          <div className="rv-who-name">
            <b>{name}</b>
            <span>{note}</span>
          </div>
          <div className="rv-who-marks">
            {rows[result].map((a) => {
              const label = agentLabel(a, fallback);
              return (
                <button
                  key={a.account}
                  type="button"
                  className="rv-mark"
                  data-testid="agent-mark"
                  ref={(el) => {
                    if (el) marks.current.set(a.account, el);
                    else marks.current.delete(a.account);
                  }}
                  data-role={a.role}
                  data-result={resultOf(a)}
                  data-working={rings && isWorking(a) ? "true" : undefined}
                  aria-label={label}
                  aria-pressed={picked === a.account}
                  onClick={() => onPick(a)}
                  onMouseEnter={(e) => setTip(tipFor(a, e.currentTarget))}
                  onFocus={(e) => {
                    focused.current = a.account;
                    setTip(tipFor(a, e.currentTarget));
                  }}
                  onMouseLeave={() => setTip(null)}
                  onBlur={(e) => {
                    // The browser can also blur a button because it unmounted.
                    if (e.relatedTarget) focused.current = null;
                    setTip(null);
                  }}
                >
                  {tip?.account === a.account && (
                    <TipText text={label} side={tip.side} />
                  )}
                </button>
              );
            })}
          </div>
          <div className="rv-who-total">
            <b data-testid="who-count">{rows[result].length}</b>
            <span data-testid="who-total">
              {rowTotal(result, rows[result])}
            </span>
          </div>
        </div>
      ))}
    </section>
  );
}
