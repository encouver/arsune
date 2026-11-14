// Client for the race host, which runs one swarm race at a time (/swarm-api, see next.config.ts).

import { ApiError } from "./bounties/api";
import type { SwarmState } from "./swarm/types";

export type Host = {
  busy: boolean;
  bounty_id: string | null;
  run_id: string | null;
  ai_available: boolean;
  ai_model: string | null;
  ai_max_usd: number;
  agents: {
    hunters: number;
    scouts: number;
    ai_hunters: number;
    ai_scouts: number;
  };
};

export const HOST_DOWN = "The hunt server is not running.";

/** The host's state, or null when no race host answers. */
export async function getHost(): Promise<Host | null> {
  try {
    const res = await fetch("/swarm-api/host", { cache: "no-store" });
    if (!res.ok) return null;
    const data = await res.json();
    return typeof data?.busy === "boolean" && data.agents ? data : null;
  } catch {
    return null;
  }
}

export async function getSwarm(): Promise<SwarmState | null> {
  try {
    const res = await fetch("/swarm-state", { cache: "no-store" });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    // The host refuses a race request without this content type (400).
    res = await fetch(path, {
      method: "POST",
      headers:
        body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, HOST_DOWN);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : null;
    // The Next proxy answers 5xx without a detail when the host is down.
    throw new ApiError(
      res.status,
      detail ??
        (res.status >= 500
          ? HOST_DOWN
          : `The hunt server answered HTTP ${res.status}.`),
    );
  }
  return data as T;
}

export function startRace(bountyId: string, aiAgents: boolean) {
  return post<{ run_id: string; bounty_id: string }>("/swarm-api/races", {
    bounty_id: bountyId,
    ai_agents: aiAgents,
  });
}

/** Ask the host to stop the race (202), then wait until it reports no race. */
export async function stopRace(timeoutMs = 15_000) {
  await post<unknown>("/swarm-api/races/stop");
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const host = await getHost();
    if (!host?.busy) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}

/** "1 scout", "12 hunters" */
export function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

export function agentCount(host: Host, ai: boolean): number {
  const a = host.agents;
  return a.hunters + a.scouts + (ai ? a.ai_hunters + a.ai_scouts : 0);
}
