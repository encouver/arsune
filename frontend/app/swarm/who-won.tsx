"use client";

import { useState, type JSX } from "react";
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
  ["lost", "Lost money", "false claims cost more than it earned"],
  ["broke", "Went broke", "lost all bond money, stopped"],
  ["none", "Nothing yet", "no proven bug so far"],
];

function rowTotal(result: Result, agents: Agent[]): string {
  if (result === "none") return "bonds back";
  return `${signedUsdc(agents.reduce((sum, a) => sum + net(a), 0))} USDC`;
}

// The tooltip opens toward the middle of the screen, so it never makes the page wider.
type Tip = { account: string; side: "left" | "right" };

function tipFor(a: Agent, el: HTMLElement): Tip {
  const box = el.getBoundingClientRect();
  const side = box.left + box.width / 2 > window.innerWidth / 2;
  return { account: a.account, side: side ? "right" : "left" };
}

export default function WhoWon({
  agents,
  fallback,
  running,
  picked,
  onPick,
}: WhoWonProps): JSX.Element | null {
  const [tip, setTip] = useState<Tip | null>(null);
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
                  data-role={a.role}
                  data-result={resultOf(a)}
                  data-working={rings && isWorking(a) ? "true" : undefined}
                  aria-label={label}
                  aria-pressed={picked === a.account}
                  onClick={() => onPick(a)}
                  onMouseEnter={(e) => setTip(tipFor(a, e.currentTarget))}
                  onFocus={(e) => setTip(tipFor(a, e.currentTarget))}
                  onMouseLeave={() => setTip(null)}
                  onBlur={() => setTip(null)}
                >
                  {tip?.account === a.account && (
                    <span
                      className="rv-tip"
                      data-side={tip.side}
                      data-testid="agent-tip"
                      role="tooltip"
                    >
                      {label}
                    </span>
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
