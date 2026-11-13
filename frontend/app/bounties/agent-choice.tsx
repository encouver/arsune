"use client";

import { count, type Host } from "../race-host";

/** Standard agents, or standard plus AI agents: only when the race host has AI agents. */
export default function AgentChoice({
  host,
  ai,
  onChange,
  disabled = false,
}: {
  host: Host | null | undefined;
  ai: boolean;
  onChange: (ai: boolean) => void;
  disabled?: boolean;
}) {
  if (!host?.ai_available) return null;
  const a = host.agents;
  return (
    <fieldset className="agent-choice" disabled={disabled}>
      <legend>Agents</legend>
      <div className="mode-options">
        <label className={!ai ? "selected" : ""}>
          <input
            type="radio"
            name="agents"
            value="standard"
            checked={!ai}
            onChange={() => onChange(false)}
          />
          <span>
            <strong>Standard agents (free)</strong>
            <small>
              {count(a.hunters, "hunter")} and {count(a.scouts, "scout")} with
              fixed rules.
            </small>
          </span>
        </label>
        <label className={ai ? "selected" : ""}>
          <input
            type="radio"
            name="agents"
            value="ai"
            checked={ai}
            onChange={() => onChange(true)}
          />
          <span>
            <strong>Standard + AI agents</strong>
            <small>AI agents also search. They use model credits.</small>
          </span>
        </label>
      </div>
    </fieldset>
  );
}
