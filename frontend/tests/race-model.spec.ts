import { expect, test } from "@playwright/test";
import {
  agentLabel,
  agentRows,
  claimsOfAgent,
  DOT_CAP,
  isWorking,
  latestSentence,
  lineCounts,
  net,
  pickNewDots,
  poolOf,
  rareText,
  resultOf,
  signedUsdc,
  stageOf,
  type LineClaim,
  type LineCounts,
  type LineLedgerRow,
} from "../app/swarm/race-model";
import type { Agent } from "../app/swarm/types";

// Unit tests of the pure race model: no browser page.

const HEX = "97458f5d4323cefe365ad6800a0238978bcbe69741fbe2371090389e6458d088";

function claim(id: string, over: Partial<LineClaim> = {}): LineClaim {
  return {
    id,
    hunter_account: "hunter45.near",
    claim_type: "horizontal_overflow",
    urls: ["https://shop.example/shop/c/support"],
    details: { url: "https://shop.example/shop/c/support" },
    status: "paid",
    outcome: "proven",
    created_at: `2026-11-12T10:00:${id.padStart(2, "0")}Z`,
    ...over,
  };
}

function row(
  kind: string,
  amount: number,
  over: Partial<LineLedgerRow> = {},
): LineLedgerRow {
  return { kind, amount, account: "x.near", status: "completed", ...over };
}

function agent(id: string, over: Partial<Agent> = {}): Agent {
  return {
    id,
    account: `${id}.near`,
    role: "hunter",
    strategy: "mobile",
    state: "searching",
    balance: 0,
    claims: 0,
    proven: 0,
    earned: 0,
    bonds_lost: 0,
    ...over,
  };
}

const NAMES: Record<string, string> = {
  "hunter45.near": "hunter-045",
  "hunter7.near": "hunter-007",
  "scout2.near": "scout-02",
};
const nameOf = (account: string) =>
  NAMES[account] ??
  (/^[0-9a-f]{64}$/.test(account) ? "an unnamed agent" : account);
const titleOf = (c: LineClaim) =>
  c.claim_type === "broken_link" ? "Broken link to /a" : "Too wide on mobile";

test("lineCounts counts each claim once, by group", () => {
  const claims = [
    claim("1", { status: "received", outcome: null }),
    claim("2", { status: "checking", outcome: null }),
    claim("3", { status: "needs_review", outcome: null }),
    claim("4", { status: "paid", outcome: "proven" }),
    claim("5", { status: "proven", outcome: "proven" }),
    claim("6", { status: "unfunded", outcome: "proven" }),
    claim("7", { status: "rejected", outcome: "rejected" }),
    claim("8", { status: "duplicate", outcome: "duplicate" }),
    claim("9", { status: "duplicate", outcome: "duplicate" }),
    claim("10", { status: "unverified", outcome: "unverified" }),
  ];
  const ledger = [
    row("reward_out", 2_140_000),
    row("scout_share", 300_000),
    row("reward_out", 1_000_000, { status: "pending" }),
    row("fund", 80_000_000),
    row("fee", 100_000),
  ];
  expect(lineCounts(claims, ledger)).toEqual({
    claims: 10,
    checking: 3,
    paid: 2,
    lost: 1,
    repeat: 2,
    unchecked: 1,
    unfunded: 1,
    paidAmount: 2_440_000,
  });
});

test("rareText names only the rare results that are not zero", () => {
  const base: LineCounts = {
    claims: 0,
    checking: 0,
    paid: 0,
    lost: 0,
    repeat: 0,
    unchecked: 0,
    unfunded: 0,
    paidAmount: 0,
  };
  expect(rareText({ ...base, unchecked: 3, unfunded: 2 })).toBe(
    "3 could not be checked · 2 proven, but the pool was empty · bond back",
  );
  expect(rareText({ ...base, unchecked: 1 })).toBe(
    "1 could not be checked · bond back",
  );
  expect(rareText({ ...base, unfunded: 1 })).toBe(
    "1 proven, but the pool was empty · bond back",
  );
  expect(rareText(base)).toBe("");
});

test("poolOf counts completed funds and lost bonds as money in", () => {
  const ledger = [
    row("fund", 70_000_000),
    row("bond_forfeit", 10_000_000),
    row("fund", 5_000_000, { status: "pending" }),
    row("reward_out", 2_000_000),
  ];
  expect(poolOf(27_190_000, ledger)).toEqual({
    into: 80_000_000,
    left: 27_190_000,
    fraction: 27_190_000 / 80_000_000,
  });
  // Review Focus 3: the edge values.
  expect(poolOf(0, []).fraction).toBeNull();
  expect(poolOf(90e6, [row("fund", 80e6)]).fraction).toBe(1);
  expect(poolOf(0, [row("fund", 80e6)]).fraction).toBe(0);
  expect(poolOf(5e6, [row("fund", 80e6, { status: "pending" })])).toEqual({
    into: 0,
    left: 5e6,
    fraction: null,
  });
});

test("stageOf follows the claim status, then its group", () => {
  const stage = (status: string, outcome: string | null = null) =>
    stageOf(claim("1", { status, outcome }));
  expect(stage("received")).toBe("claim");
  expect(stage("checking")).toBe("check");
  expect(stage("needs_review")).toBe("check");
  expect(stage("paid", "proven")).toBe("paid");
  expect(stage("proven", "proven")).toBe("paid");
  expect(stage("rejected", "rejected")).toBe("lost");
  expect(stage("duplicate", "duplicate")).toBe("repeat");
  expect(stage("unverified", "unverified")).toBe("other");
  expect(stage("unfunded", "proven")).toBe("other");
});

test("latestSentence tells the newest decided claim in plain words", () => {
  const paid = claim("5", {
    updated_at: "2026-11-12T10:09:00Z",
    scout: { account: "scout2.near" },
  });
  const pending = claim("6", {
    status: "checking",
    outcome: null,
    created_at: "2026-11-12T10:10:00Z",
  });
  const older = claim("1", {
    hunter_account: "hunter7.near",
    status: "rejected",
    outcome: "rejected",
  });
  const ledger = [
    row("reward_out", 2_140_000, { claim_id: "5", account: "hunter45.near" }),
    row("scout_share", 300_000, { claim_id: "5", account: "scout2.near" }),
    row("reward_out", 9_000_000, { claim_id: "1", account: "hunter7.near" }),
  ];
  expect(
    latestSentence([older, paid, pending], ledger, nameOf, titleOf),
  ).toEqual({
    claimId: "5",
    text: 'hunter-045 proved "Too wide on mobile" on /shop/c/support → paid +2.14 USDC · scout-02 +0.30',
  });
  expect(
    latestSentence([paid], ledger.slice(0, 1), nameOf, titleOf)?.text,
  ).toBe(
    'hunter-045 proved "Too wide on mobile" on /shop/c/support → paid +2.14 USDC',
  );
  expect(latestSentence([pending], ledger, nameOf, titleOf)).toBeNull();
  expect(latestSentence([], [], nameOf, titleOf)).toBeNull();
});

test("latestSentence says payment pending without a completed reward", () => {
  const c = claim("5", { urls: ["https://shop.example/x"], details: null });
  const pendingPay = [
    row("reward_out", 2_140_000, { claim_id: "5", status: "pending" }),
  ];
  for (const ledger of [[], pendingPay])
    expect(latestSentence([c], ledger, nameOf, titleOf)?.text).toBe(
      'hunter-045 proved "Too wide on mobile" on /x → proven, payment pending',
    );
});

test("latestSentence words the other results", () => {
  const link = (status: string, outcome: string) =>
    claim("1", {
      hunter_account: "hunter7.near",
      claim_type: "broken_link",
      urls: ["https://shop.example/b", "https://shop.example/a"],
      details: {
        source: "https://shop.example/b",
        target: "https://shop.example/a",
      },
      status,
      outcome,
    });
  const say = (status: string, outcome: string) =>
    latestSentence([link(status, outcome)], [], nameOf, titleOf)?.text;
  expect(say("rejected", "rejected")).toBe(
    'hunter-007 claimed "Broken link to /a" on /b → it was not true, bond lost',
  );
  expect(say("duplicate", "duplicate")).toBe(
    "hunter-007 sent a bug that was already proven → bond back",
  );
  expect(say("unverified", "unverified")).toBe(
    'hunter-007: Arsune could not check "Broken link to /a" → bond back',
  );
  expect(say("unfunded", "proven")).toBe(
    'hunter-007 proved "Broken link to /a", but the pool was empty → bond back',
  );
});

test("latestSentence never shows a 64-hex account (Review Focus 2)", () => {
  const c = claim("1", {
    hunter_account: HEX,
    status: "rejected",
    outcome: "rejected",
    scout: { account: HEX },
  });
  const paid = claim("2", { hunter_account: HEX, scout: { account: HEX } });
  const ledger = [
    row("reward_out", 1_000_000, { claim_id: "2", account: HEX }),
    row("scout_share", 100_000, { claim_id: "2", account: HEX }),
  ];
  for (const claims of [[c], [paid]]) {
    const text = latestSentence(claims, ledger, nameOf, titleOf)!.text;
    expect(text).toContain("an unnamed agent");
    expect(text).not.toMatch(/[0-9a-f]{64}/);
  }
});

test("resultOf keeps the board's rule", () => {
  expect(resultOf(agent("a", { state: "broke", earned: 9 }))).toBe("broke");
  expect(resultOf(agent("a", { earned: 1, bonds_lost: 2 }))).toBe("lost");
  expect(resultOf(agent("a", { earned: 2, bonds_lost: 1 }))).toBe("earned");
  expect(resultOf(agent("a"))).toBe("none");
});

test("net and signedUsdc show money with a sign", () => {
  expect(net(agent("a", { earned: 2_000_000, bonds_lost: 300_000 }))).toBe(
    1_700_000,
  );
  expect(signedUsdc(2_140_000)).toBe("+2.14");
  expect(signedUsdc(-300_000)).toBe("−0.30");
  expect(signedUsdc(0)).toBe("0.00");
});

test("agentRows groups agents by result, most money first", () => {
  const rows = agentRows([
    agent("hunter-03", { earned: 1_000_000 }),
    agent("hunter-01", { earned: 3_000_000 }),
    agent("hunter-02", { earned: 1_000_000 }),
    agent("hunter-04", { earned: 100_000, bonds_lost: 500_000 }),
    agent("hunter-05", { bonds_lost: 900_000, state: "broke" }),
    agent("hunter-06"),
  ]);
  const ids = (list: Agent[]) => list.map((a) => a.id);
  expect(ids(rows.earned)).toEqual(["hunter-01", "hunter-02", "hunter-03"]);
  expect(ids(rows.lost)).toEqual(["hunter-04"]);
  expect(ids(rows.broke)).toEqual(["hunter-05"]);
  expect(ids(rows.none)).toEqual(["hunter-06"]);
});

test("agentLabel names the agent, its strategy, tier and money", () => {
  const hunter = agent("hunter-045", { tier: "trusted", earned: 2_140_000 });
  expect(agentLabel(hunter, false)).toBe(
    "hunter-045 · mobile · trusted · +2.14 USDC",
  );
  expect(
    agentLabel(
      agent("hunter-046", { tier: "very_poor", bonds_lost: 300_000 }),
      false,
    ),
  ).toBe("hunter-046 · mobile · very poor · −0.30 USDC");
  expect(
    agentLabel(agent("hunter-ai-01", { strategy: "ai", tier: "new" }), false),
  ).toBe("hunter-ai-01 · AI · new · 0.00 USDC");
  const scout = agent("scout-02", {
    role: "scout",
    strategy: "scout",
    tier: null,
    earned: 300_000,
  });
  expect(agentLabel(scout, false)).toBe("scout-02 · scout · +0.30 USDC");
  expect(
    agentLabel({ ...scout, id: "scout-ai-01", strategy: "ai-scout" }, false),
  ).toBe("scout-ai-01 · AI · +0.30 USDC");
  const fallback = agent("hunter-01", {
    strategy: "not reported",
    earned: 2_140_000,
  });
  expect(agentLabel(fallback, true)).toBe(
    "hunter-01 · tier not reported · +2.14 USDC",
  );
  expect(agentLabel(agent("hunter-02"), true)).toBe(
    "hunter-02 · tier not reported · 0.00 USDC",
  );
});

test("isWorking is true while the agent searches, talks, claims or waits", () => {
  for (const state of ["searching", "negotiating", "claiming", "waiting"])
    expect(isWorking(agent("a", { state: state as Agent["state"] }))).toBe(
      true,
    );
  for (const state of ["broke", "done"])
    expect(isWorking(agent("a", { state: state as Agent["state"] }))).toBe(
      false,
    );
});

test("claimsOfAgent matches the hunter or the scout account", () => {
  const claims = [
    claim("1", { hunter_account: "h1.near" }),
    claim("2", { hunter_account: "h2.near", scout: { account: "s1.near" } }),
    claim("3", { hunter_account: "h1.near", scout: { account: "s1.near" } }),
    claim("4", { hunter_account: "h2.near", scout: null }),
  ];
  const ids = (list: LineClaim[]) => list.map((c) => c.id);
  expect(ids(claimsOfAgent(claims, "h1.near", "hunter"))).toEqual(["1", "3"]);
  expect(ids(claimsOfAgent(claims, "s1.near", "scout"))).toEqual(["2", "3"]);
  expect(ids(claimsOfAgent(claims, "h1.near", "scout"))).toEqual([]);
});

test("pickNewDots: no dots on first load, then only new claims, oldest first", () => {
  expect(DOT_CAP).toBe(12);
  const first = pickNewDots(null, [claim("1"), claim("2")], DOT_CAP);
  expect(first.fresh).toEqual([]);
  expect([...first.seen].sort()).toEqual(["1", "2"]);

  const next = pickNewDots(
    first.seen,
    [claim("4"), claim("3"), claim("2"), claim("1")],
    DOT_CAP,
  );
  expect(next.fresh).toEqual(["3", "4"]);
  expect([...next.seen].sort()).toEqual(["1", "2", "3", "4"]);

  expect(pickNewDots(next.seen, [claim("5")], 0).fresh).toEqual([]);
  expect(pickNewDots(next.seen, [claim("5")], -2).fresh).toEqual([]);
});

test("pickNewDots caps the dots but marks every new claim seen", () => {
  const many = Array.from({ length: 15 }, (_, i) => claim(String(i + 10)));
  const { seen, fresh } = pickNewDots(new Set(), many, 12);
  expect(fresh).toHaveLength(12);
  expect(fresh).toEqual(many.slice(0, 12).map((c) => c.id));
  expect(seen.size).toBe(15);
});
