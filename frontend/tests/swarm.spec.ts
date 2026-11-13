import { expect, Page, test } from "@playwright/test";

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
    swarm?: unknown | null;
    fail?: () => boolean;
    bounty?: Record<string, unknown>;
  } = {},
) {
  const mode = opts.mode ?? "dry";
  await page.route("**/swarm-state", (route) => {
    if (opts.fail?.() || opts.swarm === null)
      return route.fulfill({
        status: opts.swarm === null ? 502 : 500,
        body: "down",
      });
    return route.fulfill({ json: opts.swarm ?? swarm() });
  });
  await page.route("**/api/bounties", (route) =>
    route.fulfill({ json: { items: [bounty(mode)], settlement_mode: mode } }),
  );
  await page.route("**/api/bounties/*", (route) => {
    if (opts.fail?.()) return route.fulfill({ status: 500, body: "down" });
    return route.fulfill({ json: { ...bounty(mode), ...opts.bounty } });
  });
}

test("dry mode labels money as simulation and has no NearBlocks links", async ({
  page,
}) => {
  await mock(page, { mode: "dry" });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-tile")).toHaveCount(12);
  await expect(page.getByTestId("sim-label").first()).toBeVisible();
  expect(await page.getByTestId("sim-label").count()).toBeGreaterThanOrEqual(3);
  await expect(page.getByRole("link", { name: "NearBlocks" })).toHaveCount(0);
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
  await page.getByTestId("agent-tile").first().click();
  await expect(page.getByTestId("agent-detail")).toContainText("crawl forms");
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

test("near_intents mode links real payments to NearBlocks only", async ({
  page,
}) => {
  await mock(page, { mode: "near_intents" });
  await page.goto("/swarm?bounty=b1");
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
  await page.getByTestId("agent-tile").first().click();
  await expect(page.getByTestId("agent-detail")).not.toContainText(hex);
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
  // Tiles come from this bounty's records, not from the other race.
  await expect(page.getByTestId("agent-tile")).toHaveCount(2);
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
  // Tiles come from the newest bounty's records; no result colors without a race.
  await expect(page.getByTestId("agent-tile")).toHaveCount(2);
  await expect(page.getByTestId("race-results")).toHaveCount(0);
  await expect(page.getByTestId("error-banner")).toHaveCount(0);
});

test("works without the swarm runner", async ({ page }) => {
  await mock(page, { swarm: null });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-tile")).toHaveCount(2);
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
  await expect(page.getByTestId("agent-tile")).toHaveCount(12);
  down = true;
  await expect(page.getByTestId("error-banner")).toBeVisible();
  await expect(page.getByTestId("agent-tile")).toHaveCount(12);
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
  await expect(page.getByTestId("agent-tile")).toHaveCount(100);
  await expect(page.getByTestId("sim-label").first()).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("swarm-100-agents-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-100-agents-1440.png"));
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

test("mid-race tiles keep persistent red marks for lost bonds and broke", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mock(page, { swarm: { ...swarm(), agents: raceAgents(false) } });
  await page.goto("/swarm");
  const tiles = page.getByTestId("agent-tile");
  await expect(tiles).toHaveCount(7);
  const lost = tiles.nth(1);
  await expect(lost).toHaveAttribute("data-lost", "true");
  await expect(lost.getByTestId("tile-mark")).toHaveText("−0.30");
  await expect(lost.getByTestId("tile-mark")).toHaveAttribute(
    "data-kind",
    "lost",
  );
  await expect(lost).toHaveCSS("box-shadow", /rgb\(229, 67, 43\)/);
  const broke = tiles.nth(2);
  await expect(broke).toHaveAttribute("data-state", "broke");
  await expect(broke).toHaveCSS("box-shadow", /rgb\(255, 77, 51\)/);
  await expect(tiles.nth(0).getByTestId("tile-mark")).toHaveAttribute(
    "data-kind",
    "earned",
  );
  // Earned 2.00 and lost 0.30: the tile shows the net gain.
  const net = tiles.nth(4).getByTestId("tile-mark");
  await expect(net).toHaveText("+1.70");
  await expect(net).toHaveAttribute("data-kind", "earned");
  await expect(page.getByTestId("race-results")).toHaveCount(0);
  await expect(page.getByTestId("biggest-losers")).toContainText("0.9");
  await page.screenshot({
    path: testInfo.outputPath("swarm-midrace-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-midrace-1440.png"));
});

test("finished race colors tiles by result and counts them", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await mock(page, {
    swarm: { ...swarm(), finished: true, agents: raceAgents(true) },
  });
  await page.goto("/swarm");
  const tiles = page.getByTestId("agent-tile");
  await expect(tiles).toHaveCount(7);
  await expect(tiles.nth(0)).toHaveAttribute("data-result", "earned");
  await expect(tiles.nth(1)).toHaveAttribute("data-result", "lost");
  await expect(tiles.nth(2)).toHaveAttribute("data-result", "broke");
  await expect(tiles.nth(3)).toHaveAttribute("data-result", "none");
  await expect(tiles.nth(0)).toHaveCSS("background-color", "rgb(79, 155, 58)");
  await expect(tiles.nth(1)).toHaveCSS("background-color", "rgb(208, 51, 42)");
  await expect(tiles.nth(1).getByTestId("tile-mark")).toBeVisible();
  await expect(page.getByTestId("result-counts")).toHaveText(
    "3 agents earned · 2 lost money · 1 went broke · 1 found nothing",
  );
  await page.screenshot({
    path: testInfo.outputPath("swarm-finished-1440.png"),
    fullPage: true,
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-finished-1440.png"));
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
  const row = page
    .getByTestId("leaderboard")
    .locator("li", { hasText: "scout-a.testnet" });
  await expect(row).toContainText("3 proven");
  await expect(row).toContainText("1.5");
});

test("AI agents carry a badge and the AI spend shows near the totals", async ({
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
  const tiles = page.getByTestId("agent-tile");
  await expect(tiles).toHaveCount(5);
  await expect(page.getByTestId("ai-badge")).toHaveCount(2);
  await expect(tiles.nth(3)).toHaveAttribute("data-ai", "true");
  await expect(tiles.nth(0)).not.toHaveAttribute("data-ai", "true");
  await expect(tiles.nth(3)).toHaveAttribute(
    "title",
    /tier trusted · bond 0\.05 USDC/,
  );
  const usage = page.getByTestId("ai-usage");
  await expect(usage).toContainText(
    "2 AI agents think with openai/gpt-5.6-luna",
  );
  await expect(usage).toContainText("41 model calls");
  await expect(usage).toContainText("0.0123 of 0.50 USD AI budget spent");
  await tiles.nth(3).click();
  await expect(page.getByTestId("agent-detail")).toContainText(
    "decides with a language model",
  );
  await expect(page.getByTestId("agent-tier")).toHaveText(
    "trusted · bond 0.05 USDC",
  );
});

test("tile amounts up to 999.99 fit beside the scout and AI badges", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const base = { ...agents(1)[0], bonds_lost: 0 };
  await mock(page, {
    swarm: {
      ...swarm(),
      agents: [
        { ...base, id: "hunter-ai-01", strategy: "ai", earned: 13_100_000 },
        {
          ...base,
          id: "scout-ai-01",
          account: "scout-ai-1.testnet",
          role: "scout",
          strategy: "ai-scout",
          earned: 999_990_000,
        },
        {
          ...base,
          id: "hunter-02",
          account: "hunter2.testnet",
          earned: 0,
          bonds_lost: 999_990_000,
        },
      ],
    },
  });
  await page.goto("/swarm");
  const tiles = page.getByTestId("agent-tile");
  await expect(tiles).toHaveCount(3);
  const labels = ["+13.10", "+999.99", "−999.99"];
  for (const [i, label] of labels.entries()) {
    const tile = tiles.nth(i);
    const mark = tile.getByTestId("tile-mark");
    await expect(mark).toHaveText(label);
    expect(await mark.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
      true,
    );
    const t = (await tile.boundingBox())!;
    const m = (await mark.boundingBox())!;
    expect(m.x).toBeGreaterThanOrEqual(t.x);
    expect(m.x + m.width).toBeLessThanOrEqual(t.x + t.width);
    const badges = tile.locator(
      "[data-testid=ai-badge], [data-testid=scout-badge]",
    );
    for (const badge of await badges.all()) {
      const b = (await badge.boundingBox())!;
      const apart =
        b.y >= m.y + m.height ||
        b.y + b.height <= m.y ||
        b.x >= m.x + m.width ||
        b.x + b.width <= m.x;
      expect(apart).toBe(true);
    }
  }
  await expect(tiles.nth(1).getByTestId("scout-badge")).toBeVisible();
  await expect(page.getByTestId("ai-badge")).toHaveCount(2);
  await page.getByTestId("agent-grid").screenshot({
    path: testInfo.outputPath("swarm-tile-amounts-1440.png"),
  });
  console.log("SCREENSHOT", testInfo.outputPath("swarm-tile-amounts-1440.png"));
});

test("the AI block stays hidden when no AI agent runs", async ({ page }) => {
  await mock(page, {
    swarm: {
      ...swarm(),
      ai: { agents: 0, calls: 0, usd_spent: 0, budget_usd: 0, model: "" },
    },
  });
  await page.goto("/swarm");
  await expect(page.getByTestId("agent-tile")).toHaveCount(12);
  await expect(page.getByTestId("ai-usage")).toHaveCount(0);
  await expect(page.getByTestId("ai-badge")).toHaveCount(0);
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
  await expect(page.getByTestId("activity-log")).toContainText(stop);
  await expect(page.getByTestId("site-check-banner")).toHaveCount(0);
});
