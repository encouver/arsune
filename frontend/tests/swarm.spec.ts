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

function bounty(mode: "dry" | "near_intents") {
  const real = mode === "near_intents";
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
        explorer_url: real ? "https://nearblocks.io/txns/AAA" : null,
      },
      {
        id: "l2",
        kind: "reward_out",
        amount: 2_125_000,
        account: "hunter1.testnet",
        tx_id: "tx2",
        settlement_mode: mode,
        explorer_url: real ? "https://nearblocks.io/txns/BBB" : null,
      },
      {
        id: "l3",
        kind: "scout_share",
        amount: 375_000,
        account: "scout-a.testnet",
        tx_id: "tx3",
        settlement_mode: mode,
        explorer_url: real ? "https://nearblocks.io/txns/CCC" : null,
      },
      {
        id: "l4",
        kind: "fee",
        amount: 125_000,
        account: "arsune.testnet",
        tx_id: "tx4",
        settlement_mode: mode,
        explorer_url: null,
      },
      {
        id: "l5",
        kind: "bond_forfeit",
        amount: 100_000,
        account: "hunter2.testnet",
        tx_id: "tx5",
        settlement_mode: mode,
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
    mode?: "dry" | "near_intents";
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
