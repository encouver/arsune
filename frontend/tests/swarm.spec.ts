import { expect, Page, test } from "@playwright/test";

const HEX64 = /[0-9a-f]{64}/;

// Opens the closed "More details" part, where payments, tips and activity live.
async function openDetails(page: Page) {
  await page.getByTestId("more-details").locator("summary").click();
}

function markOf(page: Page, id: string) {
  return page.locator(`[data-testid="agent-mark"][aria-label^="${id} ·"]`);
}

const states = [
  "searching",
  "negotiating",
  "claiming",
  "waiting",
  "broke",
  "done",
];

function agents(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: `hunter-${String(i + 1).padStart(2, "0")}`,
    account: `hunter${i + 1}.testnet`,
    role: i % 12 === 11 ? "scout" : "hunter",
    strategy: "crawl forms and check mobile layout",
    state: states[i % states.length],
    balance: 5_000_000 - i * 1000,
    claims: i % 4,
    proven: i % 3 === 0 ? 1 : 0,
    earned: i % 3 === 0 ? 2_500_000 : 0,
    bonds_lost: i % 7 === 0 ? 100_000 : 0,
  }));
}

function swarm(n = 12) {
  return {
    run_id: "run-1",
    mode: "simulation",
    bounty_id: "b1",
    started_at: "2026-11-09T10:00:00Z",
    finished: false,
    agents: agents(n),
    tips: [
      {
        id: "t1",
        scout: "scout-a.testnet",
        claim_type: "broken_link",
        urls: ["https://shop.example/cart"],
        ask_share_bps: 2000,
        status: "sold",
        buyer: "hunter1.testnet",
        final_share_bps: 1500,
        note: "met in the middle",
      },
      {
        id: "t2",
        scout: "scout-b.testnet",
        claim_type: "layout",
        urls: ["https://shop.example/"],
        ask_share_bps: 3000,
        status: "declined",
        buyer: null,
        final_share_bps: null,
        note: null,
      },
      {
        id: "t3",
        scout: "scout-c.testnet",
        claim_type: "console_error",
        urls: ["https://shop.example/x"],
        ask_share_bps: 1000,
        status: "countered",
        buyer: "hunter2.testnet",
        final_share_bps: null,
        note: null,
      },
    ],
    log: [
      {
        at: "2026-11-09T10:00:05Z",
        agent: "hunter-01",
        text: "opened a claim on the cart page.",
      },
    ],
  };
}

type Mode = "dry" | "near_intents" | "escrow";
const HASH = "9e8d7c6b5a4f3e2d1c0b9a8f7e6d5c4b3a2f1e0d9c8b7a6f5e4d3c2b1a0f9e8d";

function bounty(mode: Mode) {
  const real = mode !== "dry";
  return {
    id: "b1",
    url: "https://shop.example",
    status: "open",
    reward_amount: 2_500_000,
    bond_amount: 100_000,
    fee_bps: 500,
    budget_amount: 50_000_000,
    budget_target: 10,
    settlement_mode: mode,
    claims: [
      {
        id: "c1",
        hunter_account: "hunter1.testnet",
        claim_type: "broken_link",
        urls: ["https://shop.example/cart", "https://shop.example/checkout"],
        details: {
          source: "https://shop.example/cart",
          target: "https://shop.example/checkout",
        },
        status: "paid",
        checker_reason: "Link returned 404 in a fresh browser.",
        evidence_hash: HASH,
        evidence_url: "/api/claims/c1/evidence",
        scout: { account: "scout-a.testnet", share_bps: 1500, tip_id: "t1" },
        created_at: "2026-11-09T10:01:00Z",
      },
      {
        id: "c2",
        hunter_account: "hunter2.testnet",
        claim_type: "layout",
        urls: ["https://shop.example/"],
        status: "rejected",
        checker_reason: "Could not reproduce the overflow.",
        scout: null,
        created_at: "2026-11-09T10:02:00Z",
      },
    ],
    ledger: [
      {
        id: "l1",
        kind: "fund",
        amount: 50_000_000,
        account: "owner.testnet",
        tx_id: "tx1",
        settlement_mode: mode,
        status: "completed",
        explorer_url: real ? "https://nearblocks.io/txns/AAA" : null,
      },
      {
        id: "l2",
        kind: "reward_out",
        amount: 2_125_000,
        account: "hunter1.testnet",
        tx_id: "tx2",
        settlement_mode: mode,
        status: "completed",
        explorer_url: real ? "https://nearblocks.io/txns/BBB" : null,
      },
      {
        id: "l3",
        kind: "scout_share",
        amount: 375_000,
        account: "scout-a.testnet",
        tx_id: "tx3",
        settlement_mode: mode,
        status: "completed",
        explorer_url: real ? "https://nearblocks.io/txns/CCC" : null,
      },
      {
        id: "l4",
        kind: "fee",
        amount: 125_000,
        account: "arsune.testnet",
        tx_id: "tx4",
        settlement_mode: mode,
        status: "completed",
        explorer_url: null,
      },
      {
        id: "l5",
        kind: "bond_forfeit",
        amount: 100_000,
        account: "hunter2.testnet",
        tx_id: "tx5",
        settlement_mode: mode,
        status: "completed",
        explorer_url: null,
      },
      {
        id: "l6",
        kind: "fund",
        amount: 7_000_000,
        account: "owner.testnet",
        tx_id: null,
        settlement_mode: mode,
        status: "failed",
        explorer_url: null,
      },
      {
        id: "l7",
        kind: "reward_out",
        amount: 3_000_000,
        account: "hunter2.testnet",
        tx_id: null,
        settlement_mode: mode,
        status: "pending",
        explorer_url: null,
      },
    ],
    hunters: [
      {
        account: "hunter1.testnet",
        claims: 1,
        proven: 1,
        rejected: 0,
        duplicate: 0,
        unverified: 0,
        pending: 0,
        earned: 2_125_000,
        bonds_lost: 0,
        scout_earned: 0,
      },
      {
        account: "hunter2.testnet",
        claims: 1,
        proven: 0,
        rejected: 1,
        duplicate: 0,
        unverified: 0,
        pending: 0,
        earned: 0,
        bonds_lost: 100_000,
        scout_earned: 0,
      },
    ],
  };
}

async function mock(
  page: Page,
  opts: {
    mode?: Mode;
    // A function is called on each poll; null answers 502.
    swarm?: unknown | null | (() => unknown | null);
    fail?: () => boolean;
    bounty?: Record<string, unknown> | (() => Record<string, unknown>);
  } = {},
) {
  const mode = opts.mode ?? "dry";
  await page.route("**/swarm-state", (route) => {
    const sw = typeof opts.swarm === "function" ? opts.swarm() : opts.swarm;
    if (opts.fail?.() || sw === null)
      return route.fulfill({
        status: sw === null ? 502 : 500,
        body: "down",
      });
    return route.fulfill({ json: sw ?? swarm() });
  });
  await page.route("**/api/bounties", (route) =>
    route.fulfill({ json: { items: [bounty(mode)], settlement_mode: mode } }),
  );
  await page.route("**/api/bounties/*", (route) => {
    if (opts.fail?.()) return route.fulfill({ status: 500, body: "down" });
    const over =
      typeof opts.bounty === "function" ? opts.bounty() : opts.bounty;
    return route.fulfill({ json: { ...bounty(mode), ...over } });
  });
}

test("dry mode labels money as simulation and has no NearBlocks links", async ({
  page,
}) => {
  await mock(page, { mode: "dry" });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(12);
  await expect(page.getByTestId("sim-label").first()).toBeVisible();
  expect(await page.getByTestId("sim-label").count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("link", { name: "NearBlocks" })).toHaveCount(0);
  await openDetails(page);
  await expect(page.getByTestId("ledger")).toContainText("Simulated");
  await expect(page.getByTestId("line-claims")).toHaveText("2");
  await expect(page.getByTestId("line-paid")).toHaveText("1");
  // Paid to hunters 2.125 plus paid to scouts 0.375.
  await expect(page.getByTestId("line-paid-amount")).toHaveText("+2.50 USDC");
  await expect(page.getByTestId("line-lost")).toHaveText("1");
  await expect(page.getByTestId("line-repeat")).toHaveText("0");
  await expect(page.getByTestId("line-checking")).toHaveCount(0);
  await expect(page.getByTestId("line-rare")).toHaveCount(0);
  // Money in is the completed fund (50.00) plus the forfeited bond (0.10).
  await expect(page.getByTestId("pool-bar")).toContainText(
    "50.00 of 50.10 USDC left",
  );
  await expect(page.getByTestId("tips")).toContainText("sold");
  await expect(page.getByTestId("tips")).toContainText("declined");
  await expect(page.getByTestId("tips")).toContainText("countered");
  await expect(page.getByTestId("tips")).toContainText("final share 15%");
  await expect(page.getByTestId("claim-stream")).toContainText(
    "Could not reproduce",
  );
  await expect(page.getByTestId("activity-log")).toContainText(
    "opened a claim",
  );
  await expect(markOf(page, "hunter-01")).toHaveAttribute(
    "aria-label",
    "hunter-01 · crawl forms and check mobile layout · +2.40 USDC",
  );
  await expect(
    page.getByRole("link", { name: "Live board", exact: true }),
  ).toHaveAttribute("aria-current", "page");
});

test("the line shows rare results in one small line", async ({ page }) => {
  const [paid, rejected] = bounty("dry").claims;
  await mock(page, {
    bounty: {
      claims: [
        paid,
        rejected,
        {
          ...rejected,
          id: "c3",
          status: "unverified",
          outcome: "unverified",
          checker_reason: "The page did not finish loading.",
        },
        { ...paid, id: "c4", status: "unfunded", outcome: "proven" },
        { ...rejected, id: "c5", status: "checking", outcome: null },
      ],
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("line-claims")).toHaveText("5");
  await expect(page.getByTestId("line-rare")).toHaveText(
    "1 could not be checked · 1 proven, but the pool was empty · bond back",
  );
  await expect(page.getByTestId("line-paid")).toHaveText("1");
  await expect(page.getByTestId("line-lost")).toHaveText("1");
  await expect(page.getByTestId("line-checking")).toHaveText("1 being checked");
  await expect(page.getByTestId("line-agents")).toHaveText("12");
  await expect(page.getByTestId("race-line")).toContainText(
    "12 agents search the site",
  );
});

test("Latest names the newest decided claim", async ({ page }) => {
  const [paid, rejected] = bounty("dry").claims;
  const ledger = bounty("dry").ledger.map((r) =>
    r.kind === "reward_out" || r.kind === "scout_share"
      ? { ...r, claim_id: "c1" }
      : r,
  );
  await mock(page, {
    bounty: {
      claims: [
        {
          ...rejected,
          id: "c3",
          status: "checking",
          outcome: null,
          created_at: "2026-11-09T10:06:00Z",
        },
        { ...paid, updated_at: "2026-11-09T10:05:00Z" },
        rejected,
      ],
      ledger,
    },
  });
  await page.goto("/swarm");
  const latest = page.getByTestId("line-latest");
  await expect(latest).toHaveText(
    'hunter-01 proved "Broken link to /checkout" on /cart → paid +2.125 USDC · scout-a.testnet +0.375',
  );
  await expect(latest).toHaveAttribute("aria-live", "polite");
  const all = page.getByTestId("line-see-all");
  await expect(all).toHaveText("See all 3 claims ↓");
  await expect(all).toHaveAttribute("href", "#claims");
  await expect(page.locator("#claims")).toContainText("Claim stream");
});

test("the header says the race state in few words", async ({ page }) => {
  await mock(page);
  await page.goto("/swarm");
  await expect(page.getByText("Race running · 12 agents")).toBeVisible();
  await expect(page.getByText("Live swarm board")).toHaveCount(0);
  await expect(page.locator("h1")).toHaveText("shop.example");
});

// A claim the race makes after the page loaded.
function newClaim(i: number, over: Record<string, unknown> = {}) {
  return {
    ...bounty("dry").claims[1],
    id: `n${i}`,
    status: "received",
    outcome: null,
    checker_reason: null,
    created_at: `2026-11-09T10:${String(10 + i).padStart(2, "0")}:00Z`,
    ...over,
  };
}

test("old claims make no dots on first load", async ({ page }) => {
  let polls = 0;
  // Received claims stay on the line: a wrong dot would stay too.
  await mock(page, {
    bounty: () => {
      polls++;
      return { claims: [newClaim(1), newClaim(2), newClaim(3)] };
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("line-claims")).toHaveText("3");
  await expect.poll(() => polls).toBeGreaterThanOrEqual(3);
  expect(await page.getByTestId("race-dot").count()).toBe(0);
});

test("a new claim makes one dot that ends in its result", async ({ page }) => {
  let polls = 0;
  const old = bounty("dry").claims;
  await mock(page, {
    bounty: () => {
      polls++;
      if (polls <= 1) return {};
      if (polls <= 4) return { claims: [newClaim(1), ...old] };
      return {
        claims: [newClaim(1, { status: "paid", outcome: "proven" }), ...old],
      };
    },
  });
  await page.goto("/swarm");
  const dot = page.locator('[data-testid="race-dot"][data-claim-id="n1"]');
  await expect(dot).toHaveCount(1);
  await expect(dot).toHaveAttribute("data-stage", "claim");
  await expect(dot).toHaveAttribute("data-stage", "paid");
  await expect(dot).toHaveCount(0);
  await expect(page.getByTestId("line-paid")).toHaveText("2");
  await expect(page.getByTestId("race-dot")).toHaveCount(0);
});

test("at most 12 dots move at once", async ({ page }) => {
  let polls = 0;
  const old = bounty("dry").claims;
  const many = Array.from({ length: 20 }, (_, i) => newClaim(i));
  await mock(page, {
    bounty: () => (++polls <= 1 ? {} : { claims: [...many, ...old] }),
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("line-claims")).toHaveText("22");
  const dots = page.getByTestId("race-dot");
  await expect.poll(() => dots.count()).toBeGreaterThan(0);
  for (let i = 0; i < 3; i++) {
    const at = polls;
    await expect.poll(() => polls).toBeGreaterThan(at);
    expect(await dots.count()).toBeLessThanOrEqual(12);
  }
  await expect(dots).toHaveCount(12);
});

test("no dots move with reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  let polls = 0;
  const old = bounty("dry").claims;
  await mock(page, {
    bounty: () =>
      ++polls <= 1 ? {} : { claims: [newClaim(1), newClaim(2), ...old] },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("line-claims")).toHaveText("4");
  const at = polls;
  await expect.poll(() => polls).toBeGreaterThan(at + 1);
  await expect(page.getByTestId("race-dot")).toHaveCount(0);
});

test("no dots move when the race is finished", async ({ page }) => {
  let polls = 0;
  const old = bounty("dry").claims;
  await mock(page, {
    swarm: { ...swarm(), finished: true },
    bounty: () =>
      ++polls <= 1 ? {} : { claims: [newClaim(1), newClaim(2), ...old] },
  });
  await page.goto("/swarm");
  await expect(page.getByText("Race finished")).toBeVisible();
  await expect(page.getByTestId("line-claims")).toHaveText("4");
  const at = polls;
  await expect.poll(() => polls).toBeGreaterThan(at + 1);
  await expect(page.getByTestId("race-dot")).toHaveCount(0);
});

test("switching bounty makes no dots for its old claims", async ({ page }) => {
  let polls = 0;
  let switched = false;
  // Received claims stay on the line: a wrong dot would stay too.
  const other = Array.from({ length: 5 }, (_, i) =>
    newClaim(i, { id: `b2-${i}` }),
  );
  await mock(page, {
    swarm: () => {
      switched = ++polls > 2;
      return { ...swarm(), bounty_id: switched ? "b2" : "b1" };
    },
    bounty: () => (switched ? { id: "b2", claims: other } : {}),
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("line-claims")).toHaveText("2");
  await expect(page.getByTestId("line-claims")).toHaveText("5");
  const at = polls;
  await expect.poll(() => polls).toBeGreaterThan(at + 1);
  expect(await page.getByTestId("race-dot").count()).toBe(0);
});

test("near_intents mode links real payments to NearBlocks only", async ({
  page,
}) => {
  await mock(page, { mode: "near_intents" });
  await page.goto("/swarm?bounty=b1");
  await openDetails(page);
  const links = page.getByRole("link", { name: "NearBlocks" });
  await expect(links).toHaveCount(3);
  await expect(links.first()).toHaveAttribute("href", /nearblocks\.io/);
  await expect(page.getByTestId("sim-label")).toHaveCount(0);
  await expect(
    page.getByText("Real settlement via NEAR Intents"),
  ).toBeVisible();
});

test("escrow mode names the escrow contract and links its transactions", async ({
  page,
}) => {
  await mock(page, { mode: "escrow" });
  await page.goto("/swarm?bounty=b1");
  await expect(
    page.getByText("Settlement by the escrow contract"),
  ).toBeVisible();
  await expect(page.getByText("Real settlement via NEAR Intents")).toHaveCount(
    0,
  );
  await expect(page.getByTestId("sim-label")).toHaveCount(0);
  await openDetails(page);
  await expect(page.getByRole("link", { name: "NearBlocks" })).toHaveCount(3);
});

test("claim rows use plain titles and keep the evidence hash in a tooltip", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/swarm");
  const paid = page.locator('[data-testid="claim-row"][data-status="paid"]');
  await expect(paid.getByTestId("claim-title")).toHaveText(
    "Broken link to /checkout",
  );
  await expect(paid).toContainText("Proven, paid");
  await expect(paid).toContainText("/cart · by hunter-01 hunter1.testnet");
  const evidence = paid.getByTestId("claim-evidence");
  await expect(evidence).toHaveAttribute("href", "/api/claims/c1/evidence");
  await expect(evidence).toHaveAttribute("title", HASH);
  expect(await paid.innerText()).not.toContain(HASH.slice(0, 12));
  const rejected = page.locator(
    '[data-testid="claim-row"][data-status="rejected"]',
  );
  await expect(rejected).toContainText("False claim");
  await expect(rejected.getByTestId("claim-evidence")).toHaveCount(0);
});

test("agents show by name; a 64-hex account stays in the tooltip", async ({
  page,
}) => {
  const hex =
    "97458f5d4323cefe365ad6800a0238978bcbe69741fbe2371090389e6458d088";
  const unnamed =
    "3ed88b639dc1beca261344b1d2d962c9ccdbdbedcc8a442c32613904e3191d75";
  const sw = swarm();
  sw.agents[0] = { ...sw.agents[0], id: "hunter-048", account: hex };
  const claims = bounty("dry").claims;
  await mock(page, {
    swarm: sw,
    bounty: {
      claims: [
        {
          ...claims[0],
          hunter_account: hex,
          claim_type: "horizontal_overflow",
          urls: ["https://shop.example/roi"],
          details: { url: "https://shop.example/roi" },
          checker_reason:
            "Fresh measurement: content width 601 pixels; viewport width 390 pixels.",
        },
        { ...claims[1], hunter_account: unnamed },
      ],
      hunters: [],
      ledger: [],
    },
  });
  await page.goto("/swarm");
  const paid = page.locator('[data-testid="claim-row"][data-status="paid"]');
  await expect(paid).toContainText("hunter-048");
  await expect(paid.getByTestId("claim-title")).toHaveText(
    "Too wide on mobile (601 px on a 390 px screen)",
  );
  await expect(
    page.locator('[data-testid="claim-row"][data-status="rejected"]'),
  ).toContainText("Unnamed agent");
  const stream = await page.getByTestId("claim-stream").innerText();
  expect(stream).not.toContain(hex.slice(0, 12));
  expect(stream).not.toContain(unnamed.slice(0, 12));
  await expect(
    page.getByTestId("claim-stream").locator(`[title="${hex}"]`),
  ).toHaveCount(1);
  const mark = markOf(page, "hunter-048");
  await expect(mark).toHaveAttribute("aria-label", /^hunter-048 · /);
  await mark.hover();
  await expect(page.getByTestId("agent-tip")).toHaveText(/^hunter-048 · /);
  expect(await page.getByTestId("who-won").innerHTML()).not.toMatch(HEX64);
  await expect(page.getByTestId("line-latest")).toContainText(
    "an unnamed agent claimed",
  );
  expect(await page.getByTestId("race-line").innerText()).not.toMatch(HEX64);
  // A hex account as the agent filter: the bar shows the name only.
  await mark.click();
  await expect(page.getByTestId("agent-filter")).toHaveText(
    "Claims by hunter-048 · Show all",
  );
  await expect(page.getByTestId("claim-row")).toHaveCount(1);
});

test("a board for one bounty says so when the race runs for another", async ({
  page,
}) => {
  await mock(page, { swarm: { ...swarm(), bounty_id: "other" } });
  await page.goto("/swarm?bounty=b1");
  const banner = page.getByTestId("other-race-banner");
  await expect(banner).toContainText(
    "The race that runs now is for another bounty.",
  );
  await expect(
    banner.getByRole("link", { name: "Watch the race that runs now" }),
  ).toHaveAttribute("href", "/swarm?bounty=other");
  await expect(
    banner.getByRole("link", { name: "Start a race for this bounty" }),
  ).toHaveAttribute("href", "/bounties/b1");
  // Agents come from this bounty's records, not from the other race.
  await expect(page.getByTestId("agent-mark")).toHaveCount(2);
  await expect(page.getByTestId("race-dot")).toHaveCount(0);
  await openDetails(page);
  await expect(page.getByTestId("tips")).toContainText("No scout tips yet.");
  await expect(page.getByText("No race for this bounty now")).toBeVisible();
});

test("an idle race host before the first race shows no race", async ({
  page,
}) => {
  await mock(page, {
    swarm: {
      ...swarm(0),
      run_id: null,
      bounty_id: null,
      finished: true,
      tips: [],
      log: [],
    },
  });
  await page.goto("/swarm");
  await expect(page.getByText("No race has run yet").first()).toBeVisible();
  // Agents come from the newest bounty's records; no rings without a race.
  await expect(page.getByTestId("agent-mark")).toHaveCount(2);
  await expect(page.locator("[data-working]")).toHaveCount(0);
  await expect(page.getByTestId("error-banner")).toHaveCount(0);
});

test("works without the swarm runner", async ({ page }) => {
  await mock(page, { swarm: null });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(2);
  await expect(page.getByTestId("claim-stream")).toContainText(
    "hunter1.testnet",
  );
  await expect(page.getByText("Swarm runner offline")).toBeVisible();
  await expect(page.getByTestId("error-banner")).toHaveCount(0);
});

test("keeps last good state on error and recovers", async ({ page }) => {
  let down = false;
  await mock(page, { fail: () => down });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(12);
  down = true;
  await expect(page.getByTestId("error-banner")).toBeVisible();
  await expect(page.getByTestId("agent-mark")).toHaveCount(12);
  await expect(page.getByTestId("claim-stream")).toContainText(
    "hunter1.testnet",
  );
  down = false;
  await expect(page.getByTestId("error-banner")).toHaveCount(0);
});

test("renders 100 agents at desktop size", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mock(page, { swarm: swarm(100) });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(100);
  await expect(page.getByTestId("sim-label").first()).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("swarm-100-agents-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-100-agents-1440.png"));
});

test("no horizontal scroll at 390 px with 110 agents", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page, { swarm: swarm(110) });
  await page.goto("/swarm");
  const marks = page.getByTestId("agent-mark");
  await expect(marks).toHaveCount(110);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  // The line is vertical: Check sits below Claim, Claim below Find.
  const line = page.getByTestId("race-line");
  const box = async (step: string) =>
    (await line.locator(`[data-step="${step}"]`).boundingBox())!;
  const [find, claim, check] = [
    await box("find"),
    await box("claim"),
    await box("check"),
  ];
  expect(claim.y).toBeGreaterThan(find.y);
  expect(check.y).toBeGreaterThan(claim.y);
  const paid = (await line.getByTestId("line-paid").boundingBox())!;
  expect(paid.y).toBeGreaterThan(check.y);
  for (const mark of await marks.all()) {
    const b = (await mark.boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(0);
    expect(b.x + b.width).toBeLessThanOrEqual(390);
  }
  // The name and count of a Who won row sit above its marks.
  const row = page.locator('[data-testid="who-row"][data-result="earned"]');
  const count = (await row.getByTestId("who-count").boundingBox())!;
  const first = (await row.getByTestId("agent-mark").first().boundingBox())!;
  expect(first.y).toBeGreaterThan(count.y);
  // A tooltip at either edge stays inside the screen.
  const rowMarks = row.getByTestId("agent-mark");
  for (const mark of [rowMarks.first(), rowMarks.nth(11), rowMarks.last()]) {
    await mark.hover();
    const tip = (await page.getByTestId("agent-tip").boundingBox())!;
    expect(tip.x).toBeGreaterThanOrEqual(0);
    expect(tip.x + tip.width).toBeLessThanOrEqual(390);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBeLessThanOrEqual(390);
  }
  await page.mouse.move(0, 0);
  await page.screenshot({
    path: testInfo.outputPath("swarm-110-agents-390.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-110-agents-390.png"));
});

function raceAgents(finished: boolean) {
  const mk = (i: number, over: Record<string, unknown>) => ({
    id: `hunter-${String(i).padStart(2, "0")}`,
    account: `hunter${i}.testnet`,
    role: "hunter",
    strategy: "s",
    state: finished ? "done" : "searching",
    balance: 4_000_000,
    claims: 0,
    proven: 0,
    earned: 0,
    bonds_lost: 0,
    ...over,
  });
  return [
    mk(1, { claims: 2, proven: 2, earned: 5_000_000 }),
    mk(2, { claims: 1, bonds_lost: 300_000 }),
    mk(3, { claims: 3, bonds_lost: 900_000, state: "broke", balance: 0 }),
    mk(4, {}),
    mk(5, { claims: 2, proven: 1, earned: 2_000_000, bonds_lost: 300_000 }),
    mk(6, { claims: 1, bonds_lost: 100_000, earned: 0 }),
    mk(7, {
      role: "scout",
      claims: 0,
      proven: 3,
      earned: 1_500_000,
    }),
  ];
}

test("Who won groups agents by result, most money first", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mock(page, {
    swarm: { ...swarm(), finished: true, agents: raceAgents(true) },
  });
  await page.goto("/swarm");
  await expect(page.getByText("Race finished")).toBeVisible();
  const marks = page.getByTestId("agent-mark");
  await expect(marks).toHaveCount(7);
  const row = (r: string) =>
    page.locator(`[data-testid="who-row"][data-result="${r}"]`);
  const labels = (r: string) =>
    row(r)
      .getByTestId("agent-mark")
      .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  expect(await labels("earned")).toEqual([
    "hunter-01 · s · +5.00 USDC",
    "hunter-05 · s · +1.70 USDC",
    "hunter-07 · s · +1.50 USDC",
  ]);
  expect(await labels("lost")).toEqual([
    "hunter-02 · s · −0.30 USDC",
    "hunter-06 · s · −0.10 USDC",
  ]);
  expect(await labels("broke")).toEqual(["hunter-03 · s · −0.90 USDC"]);
  expect(await labels("none")).toEqual(["hunter-04 · s · 0.00 USDC"]);
  const totals = [
    ["earned", "3", "+8.20 USDC"],
    ["lost", "2", "−0.40 USDC"],
    ["broke", "1", "−0.90 USDC"],
    ["none", "1", "bonds back"],
  ];
  for (const [r, count, total] of totals) {
    await expect(row(r).getByTestId("who-count")).toHaveText(count);
    await expect(row(r).getByTestId("who-total")).toHaveText(total);
  }
  await expect(row("earned")).toContainText("Earned");
  await expect(row("earned")).toContainText("proved bugs, got paid");
  await expect(row("lost")).toContainText(
    "Lost money" + "false claims cost more than it earned",
  );
  await expect(row("broke")).toContainText(
    "Went broke" + "lost all bond money, stopped",
  );
  await expect(row("none")).toContainText(
    "Nothing yet" + "no proven bug so far",
  );
  // Shapes and colors: hunters are circles, scouts rounded squares.
  const scout = markOf(page, "hunter-07");
  await expect(scout).toHaveAttribute("data-role", "scout");
  await expect(scout).toHaveCSS("border-radius", "3px");
  await expect(markOf(page, "hunter-01")).toHaveCSS("border-radius", "50%");
  await expect(markOf(page, "hunter-01")).toHaveCSS(
    "background-color",
    "rgb(47, 122, 58)",
  );
  await expect(markOf(page, "hunter-02")).toHaveCSS(
    "background-color",
    "rgb(236, 122, 92)",
  );
  const broke = markOf(page, "hunter-03");
  await expect(broke).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(broke).toHaveCSS("box-shadow", /rgb\(236, 122, 92\)/);
  await expect(page.locator("[data-working]")).toHaveCount(0);
  await expect(page.getByTestId("who-won")).not.toContainText("working now");
  await openDetails(page);
  await expect(page.getByTestId("biggest-losers")).toContainText("0.9");
  await page.screenshot({
    path: testInfo.outputPath("swarm-finished-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-finished-1440.png"));
});

test("working agents have a gold ring while the race runs", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const states = [
    "searching",
    "negotiating",
    "broke",
    "done",
    "claiming",
    "waiting",
    "searching",
  ];
  const agents = raceAgents(false).map((a, i) => ({ ...a, state: states[i] }));
  await mock(page, { swarm: { ...swarm(), agents } });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(7);
  const ringed = page.locator(
    '[data-testid="agent-mark"][data-working="true"]',
  );
  await expect(ringed).toHaveCount(5);
  for (const id of [
    "hunter-01",
    "hunter-02",
    "hunter-05",
    "hunter-06",
    "hunter-07",
  ])
    await expect(markOf(page, id)).toHaveAttribute("data-working", "true");
  for (const id of ["hunter-03", "hunter-04"])
    await expect(markOf(page, id)).not.toHaveAttribute("data-working", "true");
  const ring = await markOf(page, "hunter-01").evaluate(
    (el) => getComputedStyle(el, "::after").borderTopColor,
  );
  expect(ring).toBe("rgb(201, 154, 46)");
  await expect(page.getByTestId("who-won")).toContainText("working now");
  await page.screenshot({
    path: testInfo.outputPath("swarm-midrace-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-midrace-1440.png"));
});

test("tooltip and aria-label name the agent", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const base = { ...agents(1)[0], bonds_lost: 0, earned: 0 };
  const hex =
    "97458f5d4323cefe365ad6800a0238978bcbe69741fbe2371090389e6458d088";
  await mock(page, {
    swarm: {
      ...swarm(),
      agents: [
        {
          ...base,
          id: "hunter-045",
          account: hex,
          strategy: "mobile",
          tier: "trusted",
          earned: 2_140_000,
        },
        {
          ...base,
          id: "scout-02",
          account: "scout2.testnet",
          role: "scout",
          strategy: "links",
          tier: null,
          earned: 300_000,
        },
        {
          ...base,
          id: "hunter-ai-01",
          account: "ai1.testnet",
          strategy: "ai",
          tier: "very_poor",
          bonds_lost: 300_000,
        },
        {
          ...base,
          id: "hunter-099",
          account: "big.testnet",
          strategy: "generalist",
          tier: "standard",
          earned: 999_990_000,
        },
      ],
    },
  });
  await page.goto("/swarm");
  const tip = page.getByTestId("agent-tip");
  await expect(tip).toHaveCount(0);
  await markOf(page, "hunter-045").hover();
  await expect(tip).toHaveText("hunter-045 · mobile · trusted · +2.14 USDC");
  await expect(markOf(page, "hunter-045")).toHaveAttribute(
    "aria-label",
    "hunter-045 · mobile · trusted · +2.14 USDC",
  );
  await page.mouse.move(0, 0);
  await expect(tip).toHaveCount(0);
  // Keyboard: Tab from hunter-045 to the next mark in the row, scout-02.
  await markOf(page, "hunter-045").focus();
  await page.keyboard.press("Tab");
  await expect(markOf(page, "scout-02")).toBeFocused();
  await expect(tip).toHaveText("scout-02 · links · +0.30 USDC");
  await expect(markOf(page, "hunter-ai-01")).toHaveAttribute(
    "aria-label",
    "hunter-ai-01 · AI · very poor · −0.30 USDC",
  );
  // The biggest amount: its tooltip stays inside the window.
  await markOf(page, "hunter-099").hover();
  await expect(tip).toHaveText(
    "hunter-099 · generalist · standard · +999.99 USDC",
  );
  const box = (await tip.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(1440);
  expect(await page.getByTestId("who-won").innerHTML()).not.toMatch(HEX64);
});

test("a click on a mark filters the claim list", async ({ page }) => {
  let polls = 0;
  const sw = swarm();
  sw.agents[11] = {
    ...sw.agents[11],
    id: "scout-01",
    account: "scout-a.testnet",
    role: "scout",
  };
  const [paid, rejected] = bounty("dry").claims;
  await mock(page, {
    swarm: sw,
    bounty: () => {
      polls++;
      return {
        claims: [
          paid,
          rejected,
          { ...rejected, id: "c3", hunter_account: "hunter1.testnet" },
          {
            ...rejected,
            id: "c4",
            hunter_account: "hunter3.testnet",
            status: "duplicate",
            outcome: "duplicate",
            scout: { account: "scout-a.testnet", share_bps: 1500 },
          },
        ],
      };
    },
  });
  await page.goto("/swarm");
  const rows = page.getByTestId("claim-row");
  await expect(rows).toHaveCount(4);
  const bar = page.getByTestId("agent-filter");
  await expect(bar).toHaveCount(0);

  await markOf(page, "hunter-01").click();
  await expect(markOf(page, "hunter-01")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(bar).toHaveText("Claims by hunter-01 · Show all");
  await expect(rows).toHaveCount(2);
  for (const r of await rows.all()) await expect(r).toContainText("hunter-01");
  await expect(page.getByTestId("filter-all")).toHaveText("All (2)");
  await expect(page.getByTestId("filter-rejected")).toHaveText("False (1)");

  // The chip and the agent filter both apply, and they stay across polls.
  await page.getByTestId("filter-rejected").click();
  await expect(rows).toHaveCount(1);
  const at = polls;
  await expect.poll(() => polls).toBeGreaterThan(at + 1);
  await expect(rows).toHaveCount(1);
  await expect(bar).toBeVisible();

  await bar.getByRole("button", { name: "Show all" }).click();
  await expect(bar).toHaveCount(0);
  await expect(rows).toHaveCount(2);
  await page.getByTestId("filter-all").click();
  await expect(rows).toHaveCount(4);

  // A scout mark shows the claims that the scout sourced.
  await markOf(page, "scout-01").click();
  await expect(bar).toHaveText("Claims by scout-01 · Show all");
  await expect(rows).toHaveCount(2);
  await expect(
    page.locator('[data-testid="claim-row"][data-status="duplicate"]'),
  ).toHaveCount(1);
  // A second click on the picked mark clears the filter.
  await markOf(page, "scout-01").click();
  await expect(bar).toHaveCount(0);
  await expect(rows).toHaveCount(4);
});

test("the agent filter survives the fallback to Arsune's records", async ({
  page,
}) => {
  let phase: "race" | "down" | "idle" = "race";
  const sw = swarm();
  // Race names differ from the fallback names (hunter-01, hunter-02 by hunters order).
  sw.agents = sw.agents.map((a, i) => ({
    ...a,
    id: `hunter-${String(i + 1).padStart(3, "0")}`,
  }));
  await mock(page, {
    swarm: () =>
      phase === "race"
        ? sw
        : phase === "down"
          ? null
          : { ...swarm(0), run_id: null, bounty_id: null, finished: true },
  });
  await page.goto("/swarm");
  const rows = page.getByTestId("claim-row");
  const bar = page.getByTestId("agent-filter");
  await expect(rows).toHaveCount(2);

  // An agent with no claims.
  await markOf(page, "hunter-005").click();
  await expect(bar).toHaveText("Claims by hunter-005 · Show all");
  await expect(rows).toHaveCount(0);
  await expect(page.getByTestId("claim-stream")).toContainText(
    "No claims match.",
  );

  await markOf(page, "hunter-001").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute("data-status", "paid");

  // The page keeps the last good race state while the host does not answer.
  phase = "down";
  await expect(page.getByTestId("error-banner")).toContainText(
    "Swarm runner is not answering.",
  );
  await expect(bar).toHaveText("Claims by hunter-001 · Show all");
  await expect(rows).toHaveCount(1);

  phase = "idle";
  await expect(page.getByTestId("agent-mark")).toHaveCount(2);
  await expect(markOf(page, "hunter-01")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(bar).toHaveText("Claims by hunter-001 · Show all");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("hunter1.testnet");
});

test("fallback agents have no rings and say tier not reported", async ({
  page,
}) => {
  await mock(page, {
    swarm: { ...swarm(0), run_id: null, bounty_id: null, finished: true },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(2);
  await expect(page.locator("[data-working]")).toHaveCount(0);
  await expect(markOf(page, "hunter-01")).toHaveAttribute(
    "aria-label",
    "hunter-01 · tier not reported · +2.125 USDC",
  );
  await expect(markOf(page, "hunter-02")).toHaveAttribute(
    "aria-label",
    "hunter-02 · tier not reported · −0.10 USDC",
  );
  await expect(page.getByTestId("who-won")).not.toContainText("working now");
  await expect(page.getByTestId("line-agents")).toHaveCount(0);
  await expect(page.getByTestId("race-line")).toContainText(
    "Agents search the site",
  );
});

test("More details is closed and keeps payments, tips, leaderboard, activity", async ({
  page,
}) => {
  await mock(page);
  await page.goto("/swarm");
  const more = page.getByTestId("more-details");
  await expect(more).not.toHaveAttribute("open");
  await expect(page.getByTestId("ledger")).toBeHidden();
  await openDetails(page);
  await expect(more).toHaveAttribute("open");
  for (const id of ["ledger", "tips", "leaderboard", "activity-log"])
    await expect(page.getByTestId(id)).toBeVisible();
  await expect(page.getByTestId("pay-fees")).toHaveText(
    "Arsune fees0.125 USDC",
  );
  await expect(page.getByTestId("pay-forfeited")).toHaveText(
    "Bonds forfeited0.10 USDC",
  );
  await expect(page.getByTestId("pay-scouts")).toHaveText(
    "Paid to scouts0.375 USDC",
  );
  await expect(page.getByTestId("pay-false-cost")).toHaveText(
    "Cost of false claims to you0.00 USDC",
  );
});

test("claim stream filter shows only the chosen status", async ({ page }) => {
  await mock(page);
  await page.goto("/swarm");
  const rows = page.getByTestId("claim-row");
  await expect(rows).toHaveCount(2);
  await page.getByTestId("filter-rejected").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute("data-status", "rejected");
  await expect(rows.first()).toHaveCSS("border-left-color", "rgb(208, 51, 42)");
  await page.getByTestId("filter-paid").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute("data-status", "paid");
  await page.getByTestId("filter-duplicate").click();
  await expect(rows).toHaveCount(0);
  await expect(page.getByTestId("claim-stream")).toContainText(
    "No claims match",
  );
  await page.getByTestId("filter-all").click();
  await expect(rows).toHaveCount(2);
});

test("scouts show proven count from swarm state", async ({ page }) => {
  const sw = swarm();
  sw.agents = raceAgents(false).map((a) =>
    a.role === "scout" ? { ...a, account: "scout-a.testnet" } : a,
  ) as typeof sw.agents;
  await mock(page, { swarm: sw });
  await page.goto("/swarm");
  await openDetails(page);
  const row = page
    .getByTestId("leaderboard")
    .locator("li", { hasText: "scout-a.testnet" });
  await expect(row).toContainText("3 proven");
  await expect(row).toContainText("1.5");
});

test("AI agents say AI in their label and the AI spend shows under the pool", async ({
  page,
}) => {
  const sw = {
    ...swarm(),
    agents: [
      ...agents(3),
      {
        ...agents(1)[0],
        id: "hunter-ai-01",
        account: "hunter-ai-1.testnet",
        strategy: "ai",
        tier: "trusted",
        required_bond: 50_000,
      },
      {
        ...agents(1)[0],
        id: "scout-ai-01",
        account: "scout-ai-1.testnet",
        role: "scout",
        strategy: "ai-scout",
        tier: null,
        required_bond: null,
      },
    ],
    ai: {
      agents: 2,
      calls: 41,
      usd_spent: 0.01234,
      budget_usd: 0.5,
      model: "openai/gpt-5.6-luna",
    },
  };
  await mock(page, { swarm: sw });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(5);
  await expect(markOf(page, "hunter-ai-01")).toHaveAttribute(
    "aria-label",
    "hunter-ai-01 · AI · trusted · +2.40 USDC",
  );
  await expect(markOf(page, "scout-ai-01")).toHaveAttribute(
    "aria-label",
    "scout-ai-01 · AI · +2.40 USDC",
  );
  await expect(markOf(page, "hunter-01")).not.toHaveAttribute(
    "aria-label",
    / AI /,
  );
  const usage = page.getByTestId("ai-usage");
  await expect(usage).toContainText(
    "2 AI agents think with openai/gpt-5.6-luna",
  );
  await expect(usage).toContainText("41 model calls");
  await expect(usage).toContainText("0.0123 of 0.50 USD AI budget spent");
});

test("the AI block stays hidden when no AI agent runs", async ({ page }) => {
  await mock(page, {
    swarm: {
      ...swarm(),
      ai: { agents: 0, calls: 0, usd_spent: 0, budget_usd: 0, model: "" },
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(12);
  await expect(page.getByTestId("ai-usage")).toHaveCount(0);
});

test("a failed site check says that no agent started, and why", async ({
  page,
}) => {
  const reason =
    "Arsune cannot check this site: a fresh load of https://shop.example/ did not complete. " +
    "Script https://cdn.example/app.js: Browser policy blocked script-src-elem.";
  await mock(page, {
    swarm: {
      ...swarm(0),
      finished: true,
      site_check: { state: "failed", reason },
      log: [
        {
          at: "2026-11-09T10:00:05Z",
          agent: "swarm",
          text: `No agent starts: ${reason}`,
        },
      ],
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("site-check-banner")).toHaveText(
    `No agent started: ${reason}`,
  );
  await expect(page.getByTestId("line-claims")).toHaveText("0");
  await expect(page.getByTestId("line-paid")).toHaveText("0");
  await openDetails(page);
  await expect(page.getByTestId("activity-log")).toContainText(
    "No agent starts: Arsune cannot check this site",
  );
});

test("a paused bounty shows the stop line and Arsune's reason", async ({
  page,
}) => {
  const reason =
    "Arsune cannot check this site: the last 5 claims were unverified because Arsune could " +
    "not load their pages completely.";
  const stop =
    "The bounty is paused: Arsune cannot check this site — agents stop to save tokens.";
  await mock(page, {
    bounty: { status: "paused", paused_reason: reason },
    swarm: {
      ...swarm(),
      log: [{ at: "2026-11-09T10:00:09Z", agent: "swarm", text: stop }],
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("paused-banner")).toContainText(reason);
  await openDetails(page);
  await expect(page.getByTestId("activity-log")).toContainText(stop);
  await expect(page.getByTestId("site-check-banner")).toHaveCount(0);
});

test("the agent filter goes away when the race moves to another bounty", async ({
  page,
}) => {
  let polls = 0;
  let switched = false;
  let markPicked = false;
  await mock(page, {
    swarm: () => {
      switched = polls > 0 && markPicked;
      return { ...swarm(), bounty_id: switched ? "b2" : "b1" };
    },
    bounty: () => {
      polls++;
      return switched ? { id: "b2" } : {};
    },
  });
  await page.goto("/swarm");
  const bar = page.getByTestId("agent-filter");
  await markOf(page, "hunter-01").click();
  await expect(bar).toHaveText("Claims by hunter-01 · Show all");
  markPicked = true;
  await expect(page.getByTestId("agent-filter")).toHaveCount(0);
});

test("scouts in the records list are squares, and a click lists what they sourced", async ({
  page,
}) => {
  const base = bounty("dry").hunters;
  await mock(page, {
    swarm: { ...swarm(0), run_id: null, bounty_id: null, finished: true },
    bounty: {
      hunters: [
        ...base,
        {
          account: "scout-a.testnet",
          claims: 0,
          proven: 0,
          rejected: 0,
          duplicate: 0,
          unverified: 0,
          pending: 0,
          earned: 0,
          bonds_lost: 0,
          scout_earned: 375_000,
        },
      ],
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-mark")).toHaveCount(3);
  const scout = markOf(page, "hunter-03");
  await expect(scout).toHaveAttribute("data-role", "scout");
  await expect(scout).toHaveAttribute("aria-label", /\+0\.375 USDC/);
  const row = page.locator('[data-testid="who-row"][data-result="earned"]');
  await expect(row.locator('[data-role="scout"]')).toHaveCount(1);
  await scout.click();
  await expect(page.getByTestId("claim-row")).toHaveCount(1);
  await expect(page.getByTestId("claim-row")).toHaveAttribute(
    "data-status",
    "paid",
  );
});

test("keyboard focus stays on a mark that moves to another row", async ({
  page,
}) => {
  let moved = false;
  const sw = () => {
    const s = swarm();
    if (moved) s.agents[1] = { ...s.agents[1], earned: 1_000_000 };
    return s;
  };
  await mock(page, { swarm: sw });
  await page.goto("/swarm");
  const row = (r: string) =>
    page.locator(`[data-testid="who-row"][data-result="${r}"]`);
  await expect(row("none").locator('[aria-label^="hunter-02 ·"]')).toHaveCount(
    1,
  );
  await markOf(page, "hunter-02").focus();
  await expect(markOf(page, "hunter-02")).toBeFocused();
  moved = true;
  await expect(
    row("earned").locator('[aria-label^="hunter-02 ·"]'),
  ).toHaveCount(1);
  await expect(markOf(page, "hunter-02")).toBeFocused();
});

test("a hover tooltip closes when its mark moves to another row", async ({
  page,
}) => {
  let moved = false;
  const sw = () => {
    const s = swarm();
    if (moved) s.agents[1] = { ...s.agents[1], earned: 1_000_000 };
    return s;
  };
  await mock(page, { swarm: sw });
  await page.goto("/swarm");
  await markOf(page, "hunter-02").hover();
  await expect(page.getByTestId("agent-tip")).toHaveCount(1);
  moved = true;
  await expect(
    page
      .locator('[data-testid="who-row"][data-result="earned"]')
      .locator('[aria-label^="hunter-02 ·"]'),
  ).toHaveCount(1);
  await expect(page.getByTestId("agent-tip")).toHaveCount(0);
});

for (const width of [1440, 1024]) {
  test(`the fork lines end at the result swatches at ${width} px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mock(page);
    await page.goto("/swarm");
    await expect(page.getByTestId("line-claims")).toHaveText("2");
    const m = await page.evaluate(() => {
      const fork = document.querySelector(".rv-fork")!.getBoundingClientRect();
      const swatches = [
        ...document.querySelectorAll(".rv-results .rv-result .rv-swatch"),
      ].map((el) => el.getBoundingClientRect().left);
      return { end: fork.right, swatches };
    });
    expect(m.swatches.length).toBe(3);
    for (const left of m.swatches)
      expect(Math.abs(m.end - left)).toBeLessThan(4);
  });
}
