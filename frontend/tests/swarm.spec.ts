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
        urls: ["https://shop.example/cart"],
        status: "paid",
        checker_reason: "Link returned 404 in a fresh browser.",
        evidence_hash: HASH,
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
    return route.fulfill({ json: bounty(mode) });
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
  await expect(page.getByTestId("total-paid-to-hunters")).toContainText(
    "2.125",
  );
  await expect(page.getByTestId("total-pool-funded")).toHaveText(/50\.00/);
  await expect(page.getByTestId("total-paid-to-scouts")).toContainText("0.375");
  await expect(page.getByTestId("total-bonds-forfeited")).toContainText("0.1");
  await expect(page.getByTestId("total-proven-bugs")).toContainText("1");
  await expect(page.getByTestId("total-false-claims-rejected")).toContainText(
    "1",
  );
  await expect(
    page.getByTestId("total-cost-of-false-claims-to-the-owner"),
  ).toContainText("0");
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
    page.getByRole("link", { name: "Swarm", exact: true }),
  ).toHaveAttribute("aria-current", "page");
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

test("a decided claim shows the short evidence hash", async ({ page }) => {
  await mock(page);
  await page.goto("/swarm");
  const paid = page.locator('[data-testid="claim-row"][data-status="paid"]');
  const hash = paid.getByTestId("claim-hash");
  await expect(hash).toHaveText(`${HASH.slice(0, 12)}…`);
  await expect(hash).toHaveAttribute("title", HASH);
  await expect(paid).toContainText("evidence");
  const rejected = page.locator(
    '[data-testid="claim-row"][data-status="rejected"]',
  );
  await expect(rejected.getByTestId("claim-hash")).toHaveCount(0);
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
