// Plain words for bounty claims, shared by the bounty page and the swarm board.

export type ClaimText = {
  claim_type: string;
  status: string;
  outcome?: string | null;
  urls?: string[] | null;
  details?: Record<string, unknown> | null;
  checker_reason?: string | null;
};

export type Group = "proven" | "false" | "repeat" | "unchecked" | "pending";

export const GROUPS: Record<
  Group,
  { title: string; count: string; chip: string; mark: string }
> = {
  proven: {
    // Not "(paid)": a proven claim is unfunded when the budget ran out.
    title: "Proven bugs",
    count: "proven",
    chip: "Proven, paid",
    mark: "✓",
  },
  false: {
    title: "False claims (bond lost)",
    count: "false",
    chip: "False claim",
    mark: "✕",
  },
  repeat: {
    title: "Repeats (already found)",
    count: "repeats",
    chip: "Repeat",
    mark: "↺",
  },
  unchecked: {
    title: "Not checked (Arsune could not check)",
    count: "not checked",
    chip: "Not checked",
    mark: "?",
  },
  pending: {
    title: "Being checked",
    count: "being checked",
    chip: "Being checked",
    mark: "…",
  },
};

export const GROUP_ORDER: Group[] = [
  "pending",
  "proven",
  "false",
  "repeat",
  "unchecked",
];

const KINDS: Record<string, string> = {
  horizontal_overflow: "Too wide on mobile",
  page_error: "Script error on load",
  broken_link: "Broken link",
  broken_image: "Image does not load",
  price_mismatch: "Price differs between pages",
};

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function cut(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/** The path of a URL on the page's own site; another site keeps its host. */
export function where(url: unknown, base?: unknown): string {
  const value = text(url);
  if (!value) return "";
  try {
    const u = new URL(value);
    const sameSite = !text(base) || new URL(text(base)).origin === u.origin;
    const path = u.pathname + u.search || "/";
    return sameSite ? path : `${u.host}${path}`;
  } catch {
    return value;
  }
}

export function claimKind(type: string): string {
  return (
    KINDS[type] ??
    type.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())
  );
}

function overflow(reason?: string | null) {
  const m = /content width (\d+) pixels; viewport width (\d+) pixels/.exec(
    reason ?? "",
  );
  return m ? { content: Number(m[1]), screen: Number(m[2]) } : null;
}

export function claimTitle(c: ClaimText): string {
  const d = c.details ?? {};
  switch (c.claim_type) {
    case "horizontal_overflow": {
      const size = overflow(c.checker_reason);
      return size && size.content > size.screen
        ? `Too wide on mobile (${size.content} px on a ${size.screen} px screen)`
        : KINDS.horizontal_overflow;
    }
    case "page_error": {
      // The checker quotes the message as the browser raised it; details hold it lowercased.
      const raised =
        /raised an uncaught (?:error|promise rejection): ([\s\S]+)$/.exec(
          c.checker_reason ?? "",
        )?.[1];
      const message = raised || text(d.message);
      return message
        ? `Script error on load: ${cut(message, 90)}`
        : KINDS.page_error;
    }
    case "broken_link": {
      const target = text(d.target) || c.urls?.[1];
      return target
        ? `Broken link to ${where(target, d.source || c.urls?.[0])}`
        : KINDS.broken_link;
    }
    case "broken_image": {
      const image = text(d.image);
      return image
        ? `Image does not load: ${cut(where(image, d.page), 70)}`
        : KINDS.broken_image;
    }
    default:
      return claimKind(c.claim_type);
  }
}

/** The page where a visitor meets the fault. */
export function claimPage(c: ClaimText): string {
  const d = c.details ?? {};
  const urls = c.urls ?? [];
  switch (c.claim_type) {
    case "broken_link":
      return where(d.source || urls[0]);
    case "horizontal_overflow":
      return where(d.url || urls[0]);
    case "page_error":
    case "broken_image":
      return where(d.page || urls[0]);
    case "price_mismatch":
      return urls.map((u) => where(u)).join(" and ");
    default:
      return where(urls[0]) || "/";
  }
}

export function claimGroup(c: ClaimText): Group {
  if (["received", "checking", "needs_review"].includes(c.status))
    return "pending";
  if (c.status === "paid" || c.status === "unfunded") return "proven";
  const result = c.outcome ?? c.status;
  if (result === "proven") return "proven";
  if (result === "rejected") return "false";
  if (result === "duplicate") return "repeat";
  if (result === "unverified") return "unchecked";
  return "pending";
}

/** The checker's own sentence, with claim IDs replaced by words. */
export function claimReason(c: ClaimText): string {
  const reason = (c.checker_reason ?? "").replace(
    new RegExp(`\\bClaim ${UUID.source}`, "gi"),
    "An earlier claim",
  );
  if (reason) return reason;
  return claimGroup(c) === "pending"
    ? "Arsune is checking this claim in a fresh browser."
    : "";
}

/** Titles for a list of claims: a repeat borrows the measurement of the claim that proved it. */
export function titler(
  claims: (ClaimText & { fingerprint?: string | null })[],
) {
  const proved = new Map<string, ClaimText>();
  for (const c of claims)
    if (c.fingerprint && claimGroup(c) === "proven")
      proved.set(c.fingerprint, c);
  return (c: ClaimText & { fingerprint?: string | null }) => {
    const source =
      c.fingerprint && claimGroup(c) === "repeat"
        ? proved.get(c.fingerprint)
        : undefined;
    return claimTitle(
      source ? { ...c, checker_reason: source.checker_reason } : c,
    );
  };
}

export function countGroups(claims: ClaimText[]): Record<Group, number> {
  const counts = { proven: 0, false: 0, repeat: 0, unchecked: 0, pending: 0 };
  for (const c of claims) counts[claimGroup(c)]++;
  return counts;
}

/** "7 proven · 3 false · 4 repeats", with the empty optional groups left out. */
export function countText(counts: Record<Group, number>): string {
  const text = (g: Group) =>
    `${counts[g]} ${g === "repeat" && counts[g] === 1 ? "repeat" : GROUPS[g].count}`;
  const parts = (["proven", "false", "repeat"] as Group[]).map(text);
  for (const g of ["unchecked", "pending"] as Group[])
    if (counts[g]) parts.push(text(g));
  return parts.join(" · ");
}

/** NEAR implicit accounts are 64 hex characters: not a name a person reads. */
export function isHexAccount(account: string): boolean {
  return /^[0-9a-f]{64}$/.test(account);
}
