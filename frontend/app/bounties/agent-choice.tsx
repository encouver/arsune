"use client";

import { aiOffReason, count, type Host } from "../race-host";

/** Free scripted agents, or scripted plus AI agents with a spending cap. */
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
  const off = aiOffReason(host);
  const a = host?.agents;
  const maxUsd = `$${(host?.ai_max_usd ?? 0).toFixed(2)}`;
  return (
    <fieldset className="agent-choice" disabled={disabled}>
      <legend>Agents</legend>
      <div className="mode-options">
        <label className={!ai ? "selected" : ""}>
          <input
            type="radio"
            name="agents"
            value="scripted"
            checked={!ai}
            onChange={() => onChange(false)}
          />
          <span>
            <strong>Free scripted agents</strong>
            <small>
              {a
                ? `${count(a.hunters, "hunter")} and ${count(a.scouts, "scout")} follow fixed rules. No model cost.`
                : "Agents follow fixed rules. No model cost."}
            </small>
          </span>
        </label>
        <label className={ai ? "selected" : ""}>
          <input
            type="radio"
            name="agents"
            value="ai"
            checked={ai && !off}
            disabled={!!off}
            onChange={() => onChange(true)}
          />
          <span>
            <strong>
              {off
                ? "Scripted + AI agents"
                : `Scripted + AI agents (max ${maxUsd}, model ${host?.ai_model})`}
            </strong>
            <small data-testid="ai-option-note">
              {off ??
                `Adds ${count(a?.ai_hunters ?? 0, "AI hunter")} and ${count(a?.ai_scouts ?? 0, "AI scout")}. Arsune stops them at ${maxUsd}.`}
            </small>
          </span>
        </label>
      </div>
    </fieldset>
  );
}
