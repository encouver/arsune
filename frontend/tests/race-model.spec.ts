import { expect, test } from "@playwright/test";
import {
  agentLabel,
  agentRows,
  claimsOfAgent,
  claimsOfRace,
  DOT_CAP,
  finishedStory,
  isWorking,
  latestSentence,
  ledgerOfClaims,
  lineCounts,
  net,
  pickNewDots,
  poolOf,
  rareText,
  resultOf,
  resultStory,
  runningStory,
  signedUsdc,
  stageOf,
  withRecords,
  type LineClaim,
  type LineCounts,
  type LineLedgerRow,
} from "../app/swarm/race-model";
import type { Agent, HunterStat } from "../app/swarm/types";

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
    paid: 1,
    paying: 1,
    lost: 1,
    repeat: 2,
    unchecked: 1,
    unfunded: 1,
    paidAmount: 2_440_000,
  });
});

const base: LineCounts = {
  claims: 0,
  checking: 0,
  paid: 0,
  paying: 0,
  lost: 0,
  repeat: 0,
  unchecked: 0,
  unfunded: 0,
  paidAmount: 0,
};

test("rareText names only the rare results that are not zero", () => {
  expect(rareText({ ...base, unchecked: 3, unfunded: 2 })).toBe(
    "3 could not be checked · 2 proven, but the budget ran out · deposit back",
  );
  expect(rareText({ ...base, unchecked: 1 })).toBe(
    "1 could not be checked · deposit back",
  );
  expect(rareText({ ...base, unfunded: 1 })).toBe(
    "1 proven, but the budget ran out · deposit back",
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
    'hunter-007 reported "Broken link to /a" on /b → it was fake, deposit lost',
  );
  expect(say("duplicate", "duplicate")).toBe(
    "hunter-007 sent a bug that was already proven → deposit back",
  );
  expect(say("unverified", "unverified")).toBe(
    'hunter-007: Arsune could not check "Broken link to /a" → deposit back',
  );
  expect(say("unfunded", "proven")).toBe(
    'hunter-007 proved "Broken link to /a", but the budget ran out → deposit back',
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

test("latestSentence leaves out the page when the claim has none", () => {
  const c = claim("1", { urls: [], details: null });
  for (const status of ["paid", "rejected"]) {
    const text = latestSentence(
      [{ ...c, status, outcome: status === "paid" ? "proven" : "rejected" }],
      [],
      nameOf,
      titleOf,
    )?.text;
    expect(text).not.toMatch(/ on\s*(→|$)/);
    expect(text).not.toContain("  ");
  }
});

function record(account: string, over: Partial<HunterStat> = {}): HunterStat {
  return {
    account,
    claims: 0,
    proven: 0,
    rejected: 0,
    duplicate: 0,
    unverified: 0,
    pending: 0,
    earned: 0,
    bonds_lost: 0,
    scout_earned: 0,
    ...over,
  };
}

test("withRecords takes money from Arsune's records and state from the race host", () => {
  // The race host stopped counting: it says 0. Arsune's records say 2.00 earned.
  const host = [
    agent("hunter-01", { earned: 0, state: "done" }),
    agent("hunter-02", { earned: 700_000, bonds_lost: 100_000 }),
    agent("scout-01", { role: "scout", earned: 0 }),
    agent("hunter-03", { bonds_lost: 300_000, state: "broke" }),
  ];
  const records = [
    record("hunter-01.near", { earned: 2_000_000 }),
    record("scout-01.near", { earned: 100_000, scout_earned: 400_000 }),
    record("hunter-03.near", { bonds_lost: 300_000 }),
  ];
  const merged = withRecords(host, records);
  const [hunter, unlisted, scout, broke] = merged;
  expect(hunter).toEqual({ ...host[0], earned: 2_000_000, bonds_lost: 0 });
  expect(agentRows(merged).earned.map((a) => a.id)).toContain("hunter-01");
  expect(agentLabel(hunter, false)).toBe("hunter-01 · mobile · +2.00 USDC");
  // An agent the API does not list keeps the host's numbers.
  expect(unlisted).toEqual(host[1]);
  // A scout's money is its own reward plus its tip shares.
  expect(scout.earned).toBe(500_000);
  // The state still comes from the race host.
  expect(resultOf(broke)).toBe("broke");
  expect(hunter.state).toBe("done");
  expect(host[0].earned).toBe(0);
});

test("runningStory names the agents and the site", () => {
  expect(runningStory(110, "shop.example/shop/")).toBe(
    "110 agents search shop.example/shop/. Each report needs a deposit. Arsune checks it in a clean browser.",
  );
  expect(runningStory(1, "shop.example")).toBe(
    "1 agent searches shop.example. Each report needs a deposit. Arsune checks it in a clean browser.",
  );
});

test("finishedStory tells the result and leaves out false claims when there are none", () => {
  expect(
    finishedStory({ ...base, paid: 7, lost: 3, paidAmount: 15_400_000 }),
  ).toBe(
    "7 bugs proven and paid (15.40 USDC). 3 fake reports lost their deposit. Fake reports cost the owner nothing.",
  );
  expect(
    finishedStory({ ...base, paid: 1, lost: 1, paidAmount: 2_140_000 }),
  ).toBe(
    "1 bug proven and paid (2.14 USDC). 1 fake report lost its deposit. Fake reports cost the owner nothing.",
  );
  expect(finishedStory({ ...base, paid: 2, paidAmount: 4_000_000 })).toBe(
    "2 bugs proven and paid (4.00 USDC).",
  );
  // The first result sentence stays, even at 0.
  expect(finishedStory({ ...base, lost: 2 })).toBe(
    "0 bugs proven and paid (0.00 USDC). 2 fake reports lost their deposit. Fake reports cost the owner nothing.",
  );
  // Repeats and unfunded claims are not part of this line; unchecked claims are.
  expect(finishedStory({ ...base, repeat: 4, unfunded: 1, unchecked: 2 })).toBe(
    "0 bugs proven and paid (0.00 USDC). 2 could not be checked: their deposits came back.",
  );
  expect(finishedStory({ ...base, unchecked: 1 })).toBe(
    "0 bugs proven and paid (0.00 USDC). 1 could not be checked: its deposit came back.",
  );
});

test("resultStory tells the owner the outcome, leaving out counts of 0", () => {
  expect(
    resultStory({
      ...base,
      paid: 5,
      unfunded: 2,
      lost: 3,
      repeat: 4,
      paidAmount: 11_000_000,
    }),
  ).toBe(
    "Your hunt found 7 bugs. 5 are paid (11.00 USDC). 2 were not paid: the budget ran out. " +
      "3 fake reports cost you nothing: their deposits went into your budget. 4 repeats got their deposit back.",
  );
  expect(
    resultStory({
      ...base,
      paid: 1,
      unfunded: 1,
      lost: 1,
      repeat: 1,
      paidAmount: 2_000_000,
    }),
  ).toBe(
    "Your hunt found 2 bugs. 1 is paid (2.00 USDC). 1 was not paid: the budget ran out. " +
      "1 fake report cost you nothing: its deposit went into your budget. 1 repeat got its deposit back.",
  );
  expect(resultStory({ ...base, paid: 1, paidAmount: 1_000_000 })).toBe(
    "Your hunt found 1 bug. 1 is paid (1.00 USDC).",
  );
  expect(resultStory({ ...base, lost: 2 })).toBe(
    "Your hunt found 0 bugs. 2 fake reports cost you nothing: their deposits went into your budget.",
  );
  expect(resultStory({ ...base, unchecked: 1 })).toBe(
    "Your hunt found 0 bugs. 1 could not be checked: its deposit came back.",
  );
  expect(resultStory({ ...base, unchecked: 2 })).toBe(
    "Your hunt found 0 bugs. 2 could not be checked: their deposits came back.",
  );
  // Proven claims that wait for their payment are found, not paid.
  expect(
    resultStory({ ...base, paid: 1, paying: 2, paidAmount: 1_000_000 }),
  ).toBe(
    "Your hunt found 3 bugs. 1 is paid (1.00 USDC). 2 payments are on the way.",
  );
  expect(resultStory({ ...base, paying: 1 })).toBe(
    "Your hunt found 1 bug. 1 payment is on the way.",
  );
  // No story before a claim has a result.
  expect(resultStory({ ...base, claims: 3, checking: 3 })).toBeNull();
});

test("a proven claim counts as paid only when its payout is complete", () => {
  const claims = [
    claim("1", { status: "paid", outcome: "proven" }),
    claim("2", { status: "proven", outcome: "proven" }),
    claim("3", { status: "proven", outcome: "proven" }),
  ];
  const counts = lineCounts(claims, [row("reward_out", 1_000_000)]);
  expect(counts.paid).toBe(1);
  expect(counts.paying).toBe(2);
  expect(rareText(counts)).toBe("2 proven, payment on the way");
  expect(rareText({ ...base, paying: 1 })).toBe("1 proven, payment on the way");
});

test("claimsOfRace and ledgerOfClaims keep one race", () => {
  const old = claim("old", { created_at: "2026-11-09T09:59:59Z" });
  const mine = claim("mine", { created_at: "2026-11-09T10:00:00Z" });
  const race = claimsOfRace([old, mine], "2026-11-09T10:00:00Z");
  expect(race.map((c) => c.id)).toEqual(["mine"]);
  const ledger = [
    { ...row("reward_out", 1), claim_id: "old" },
    { ...row("reward_out", 2), claim_id: "mine" },
    row("fund", 3),
  ];
  expect(ledgerOfClaims(ledger, race).map((r) => r.amount)).toEqual([2]);
});

test("the stories count with claimGroup and the summary line's money", () => {
  const claims = [
    claim("1", { status: "paid", outcome: "proven" }),
    claim("2", { status: "unfunded", outcome: "proven" }),
    claim("3", { status: "rejected", outcome: "rejected" }),
    claim("4", { status: "duplicate", outcome: "duplicate" }),
    claim("5", { status: "checking", outcome: null }),
  ];
  const ledger = [
    row("reward_out", 1_700_000),
    row("scout_share", 300_000),
    row("reward_out", 900_000, { status: "pending" }),
    row("fee", 200_000),
  ];
  const counts = lineCounts(claims, ledger);
  expect(resultStory(counts)).toBe(
    "Your hunt found 2 bugs. 1 is paid (2.00 USDC). 1 was not paid: the budget ran out. " +
      "1 fake report cost you nothing: its deposit went into your budget. 1 repeat got its deposit back.",
  );
  expect(finishedStory(counts)).toBe(
    "1 bug proven and paid (2.00 USDC). 1 fake report lost its deposit. Fake reports cost the owner nothing.",
  );
});
