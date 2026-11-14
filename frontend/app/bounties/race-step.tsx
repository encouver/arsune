"use client";

import { useCallback, useEffect, useState } from "react";
import {
  claimGroup,
  claimPage,
  isHexAccount,
  titler,
  GROUPS,
} from "../claim-text";
import {
  agentCount,
  agentMix,
  getHost,
  getSwarm,
  HOST_DOWN,
  type Host,
  startRace,
  stopRace,
} from "../race-host";
import RaceLine from "../swarm/race-line";
import type { SwarmState } from "../swarm/types";
import { errorText, OwnerBounty, siteName } from "./api";
import { Message } from "./ui";

const POLL_MS = 3000;

export type Race = {
  host: Host | null | undefined;
  // The race state when the latest race is for this bounty; null otherwise.
  swarm: SwarmState | null;
  refresh: () => Promise<void>;
};

export type RacePhase =
  "locked" | "loading" | "offline" | "other" | "ready" | "running" | "finished";

export function useRace(bountyId: string): Race {
  const [host, setHost] = useState<Host | null | undefined>(undefined);
  const [swarm, setSwarm] = useState<SwarmState | null>(null);

  const refresh = useCallback(async () => {
    const h = await getHost();
    const s = h ? await getSwarm() : null;
    setHost(h);
    setSwarm(s && s.bounty_id === bountyId ? s : null);
  }, [bountyId]);

  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      await refresh();
      if (!stop) timer = setTimeout(tick, POLL_MS);
    }
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  return { host, swarm, refresh };
}

export function racePhase(
  race: Race,
  bountyId: string,
  ready: boolean,
): RacePhase {
  const { host, swarm } = race;
  if (host?.busy && host.bounty_id === bountyId) return "running";
  if (!ready) return "locked";
  if (host === undefined) return "loading";
  if (host === null) return "offline";
  if (host.busy) return "other";
  if (swarm?.finished) return "finished";
  return "ready";
}

export const PHASE_STATUS: Record<RacePhase, string> = {
  locked: "After funding",
  loading: "Asking the hunt server…",
  offline: "Hunt server offline",
  other: "Another hunt runs",
  ready: "Ready to start",
  running: "Running",
  finished: "Finished",
};

export function RaceStep({
  bounty,
  race,
  phase,
  lockedText,
}: {
  bounty: OwnerBounty;
  race: Race;
  phase: RacePhase;
  lockedText: string;
}) {
  const { host, swarm, refresh } = race;
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const canStart = phase === "ready" || phase === "finished";

  async function act(kind: "start" | "stop") {
    setBusy(kind);
    setError(null);
    try {
      if (kind === "start") await startRace(bounty.id);
      else await stopRace();
      await refresh();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  if (phase === "running")
    return (
      <>
        <LiveRace
          bounty={bounty}
          swarm={
            // The state of an earlier run of this bounty is not this race.
            swarm && host?.run_id && swarm.run_id !== host.run_id ? null : swarm
          }
        />
        <div className="bo-actions">
          <a
            className="bo-board-link"
            href={`/swarm?bounty=${encodeURIComponent(bounty.id)}`}
            data-testid="board-link"
          >
            Watch the hunt live →
          </a>
          <button
            type="button"
            className="bo-btn bo-btn-quiet"
            onClick={() => act("stop")}
            disabled={!!busy}
          >
            {busy === "stop" ? "Stopping…" : "Stop the hunt"}
          </button>
        </div>
        <Message kind="error" text={error} testId="race-error" />
      </>
    );

  return (
    <>
      {phase === "locked" && (
        <p className="bo-muted" data-testid="race-locked">
          {lockedText}
        </p>
      )}
      {phase === "offline" && (
        <Message kind="error" testId="host-down" text={HOST_DOWN} />
      )}
      {phase === "other" && (
        <p className="bo-msg bo-msg-info" data-testid="host-busy">
          Another hunt is running now. You can start yours when it ends.{" "}
          <a
            className="bo-link"
            href={`/swarm?bounty=${encodeURIComponent(host?.bounty_id ?? "")}`}
          >
            Watch that hunt
          </a>
        </p>
      )}
      {phase === "finished" && swarm && <Finished swarm={swarm} />}
      <div className="bo-actions">
        <button
          type="button"
          className="bo-btn"
          onClick={() => act("start")}
          disabled={!canStart || !!busy}
        >
          {busy === "start"
            ? "Starting…"
            : phase === "finished"
              ? "Start a new hunt"
              : "Start the hunt"}
        </button>
        {host && canStart && (
          <span className="bo-muted" data-testid="race-hint">
            {agentCount(host)} agents ({agentMix(host)}) will search{" "}
            {siteName(bounty.url)}. You pay only for proven bugs.
            {host.ai_note ? ` ${host.ai_note}` : ""}
          </span>
        )}
      </div>
      <Message kind="error" text={error} testId="race-error" />
      {phase === "finished" && (
        <a
          className="bo-link"
          href={`/swarm?bounty=${encodeURIComponent(bounty.id)}`}
          data-testid="board-link"
        >
          See the last hunt →
        </a>
      )}
    </>
  );
}

function Finished({ swarm }: { swarm: SwarmState }) {
  const failed = swarm.site_check?.state === "failed";
  return failed ? (
    <Message
      kind="error"
      testId="race-finished"
      text={`The last hunt did not start: ${swarm.site_check?.reason ?? "Arsune could not load the site."}`}
    />
  ) : (
    <Message
      kind="ok"
      testId="race-finished"
      text="The last hunt ended. Its results are at the top of this page."
    />
  );
}

/** The summary line and the newest claims of the race that runs now, for this bounty only. */
function LiveRace({
  bounty,
  swarm,
}: {
  bounty: OwnerBounty;
  swarm: SwarmState | null;
}) {
  // Only claims made since this race started; none until its state arrives.
  const since = swarm ? Date.parse(swarm.started_at) : NaN;
  const claims = Number.isNaN(since)
    ? []
    : bounty.claims.filter((c) => Date.parse(c.created_at ?? "") >= since);
  const titleOf = titler(bounty.claims);
  const loading = swarm?.site_check?.state === "checking";
  // Only this race's money: ledger rows of its claims.
  const ids = new Set(claims.map((c) => c.id));
  const ledger = bounty.ledger.filter((r) => r.claim_id && ids.has(r.claim_id));
  const names = new Map((swarm?.agents ?? []).map((a) => [a.account, a.id]));
  const nameOf = (account: string) =>
    names.get(account) ??
    (isHexAccount(account) ? "an unnamed agent" : account);
  return (
    <div className="bo-live" data-testid="live-race">
      <p className="bo-live-head">
        <i className="bo-live-dot" aria-hidden="true" />
        {!swarm
          ? "Starting the hunt…"
          : loading
            ? "Arsune is loading your site. Agents start when it loads."
            : "The hunt is running."}
      </p>
      <RaceLine
        claims={claims}
        ledger={ledger}
        agentCount={swarm ? swarm.agents.length : null}
        nameOf={nameOf}
        titleOf={titleOf}
        finished={!!swarm?.finished}
        seeAllHref={`/swarm?bounty=${encodeURIComponent(bounty.id)}`}
        // Claims that exist when the race state arrives are old: they get no dot.
        resetKey={swarm ? `${bounty.id}:${swarm.run_id}` : bounty.id}
      />
      <ol className="bo-live-stream" data-testid="live-stream">
        {claims.length === 0 && (
          <li className="sw-empty">No reports yet. The agents are searching.</li>
        )}
        {[...claims]
          .reverse()
          .slice(0, 8)
          .map((c) => {
            const g = claimGroup(c);
            return (
              <li key={c.id} data-group={g}>
                <span className="bo-result-mark" data-group={g} aria-hidden>
                  {GROUPS[g].mark}
                </span>
                <code className="bo-result-page">{claimPage(c)}</code>
                <span className="bo-live-title">{titleOf(c)}</span>
                <span className={`sw-chip bo-chip-${g}`}>{GROUPS[g].chip}</span>
              </li>
            );
          })}
      </ol>
    </div>
  );
}
