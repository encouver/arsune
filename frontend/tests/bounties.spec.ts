import { expect, Page, Route, test } from "@playwright/test";

const ID = "6f1c9a52-8c1e-4d39-9a43-2b7f0c5d1e10";
const TOKEN = "Qk3pYz0aLxVwq8RtNm2cB7sDfGh4JkLp";
const USDC =
  "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1";

const ESCROW = "escrow.arsune.near";
const HASH = "3f1a9c0b7d2e4f6a8b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a";
const FINGERPRINT =
  "742d33bd09a7249bb994caeccf5495a3456c635b80f57e0f29a3725c79fd4ddb";
const HUNTER =
  "b40d96d45a1d243c76327d13f7526bda3de12fa598eb7cf3d8ae01951eddb050";

type Mode = "dry" | "near_intents" | "escrow";

function options(mode: Mode, demo = "http://127.0.0.1:8541") {
  const live = mode === "near_intents";
  return {
    settlement_mode: mode,
    arsune_account: mode === "dry" ? "arsune.dry" : "arsune.near",
    token: USDC,
    token_symbol: "USDC",
    token_decimals: 6,
    intents_contract: "intents.near",
    escrow_contract: mode === "escrow" ? ESCROW : null,
    demo_site_url: demo,
    demo_site_origin: demo,
    simulated_funding: mode === "dry",
    crosschain: live
      ? {
          available: true,
          reason: null,
          tokens: [
            {
              asset_id:
                "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near",
              symbol: "USDC",
              blockchain: "base",
              decimals: 6,
              price: 1,
              contract_address: "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            },
            {
              asset_id: "nep141:eth.omft.near",
              symbol: "ETH",
              blockchain: "eth",
              decimals: 18,
              price: 3000,
              contract_address: null,
            },
          ],
        }
      : {
          available: false,
          reason: `Funding from another chain needs live settlement on NEAR Intents. This Arsune runs in ${mode === "dry" ? "dry mode, where no real money can arrive. Use simulated funding instead." : "escrow mode, where funds go to the escrow contract. Use a signed deposit instead."}`,
          tokens: [],
        },
  };
}

function makeBounty(mode: Mode, url: string) {
  const origin = new URL(url).origin;
  return {
    id: ID,
    url,
    origin,
    owner_account: "demo-owner.near",
    status: "pending_proof",
    closed_reason: null as string | null,
    paused_reason: null as string | null,
    site_check: null as Record<string, unknown> | null,
    reward_amount: 1_000_000,
    bond_amount: 100_000,
    fee_bps: 1000,
    fee_amount: 100_000,
    budget_amount: 0,
    budget_target: 10_000_000,
    settlement_mode: mode,
    proof_token: TOKEN,
    proof_url: `${origin}/.well-known/arsune-bounty.txt`,
    created_at: "2026-11-09T10:00:00Z",
    claims: [] as Record<string, unknown>[],
    ledger: [] as Record<string, unknown>[],
    hunters: [],
  };
}

// The race host contract: GET /swarm-api/host, POST /swarm-api/races and /races/stop.
function host(over: Record<string, unknown> = {}) {
  return {
    busy: false,
    bounty_id: null as string | null,
    run_id: null as string | null,
    ai_available: true,
    ai_model: "openai/gpt-5.6-luna",
    ai_max_usd: 0.5,
    agents: { hunters: 12, scouts: 3, ai_hunters: 3, ai_scouts: 1 },
    ...over,
  };
}

type Api = {
  bounty: ReturnType<typeof makeBounty>;
  calls: { method: string; path: string; body: unknown }[];
  funding: Record<string, unknown>[];
  verifyError?: string;
  quoteError?: string;
  // What Arsune's site check cannot load; unset, the check passes.
  siteProblem?: string;
  readsUntilChecked: number;
  // null: no race host answers.
  host: ReturnType<typeof host> | null;
  // A refusal for POST /swarm-api/races, such as a busy host.
  raceRefusal?: { status: number; detail: string };
  // Host reads left until a stopped race ends.
  stopping?: number;
  swarm: Record<string, unknown> | null;
};

const UNLOADED =
  "Arsune cannot check this site: a fresh load of http://127.0.0.1:8541/shop/ did not complete. " +
  "Script https://cdn.example/app.js: Private and local network destinations are not permitted.";

function checking(state: Api) {
  const b = state.bounty;
  b.site_check = {
    job_id: `j${state.calls.length}`,
    state: "checking",
    url: b.url,
    reason: `Arsune is loading ${b.url} in a fresh browser.`,
    problems: [],
    note: null,
    checked_at: null,
    fresh_until: null,
  };
  state.readsUntilChecked = 3;
}

// Arsune's worker runs the site check; here it ends on the third read of the bounty.
function finishSiteCheck(state: Api) {
  const b = state.bounty;
  if (b.site_check?.state !== "checking" || --state.readsUntilChecked > 0)
    return;
  if (state.siteProblem) {
    b.site_check = {
      ...b.site_check,
      state: "failed",
      reason: state.siteProblem,
    };
    return;
  }
  b.site_check = {
    ...b.site_check,
    state: "passed",
    reason: `Arsune loaded ${b.url} completely at 1280 and 390 pixels wide.`,
    checked_at: "2026-11-09T10:01:00Z",
  };
  if (b.status !== "closed")
    Object.assign(b, { status: "open", paused_reason: null });
}

function fundRow(amount: number) {
  return {
    id: `f${amount}`,
    claim_id: null,
    kind: "fund",
    amount,
    account: "a".repeat(64),
    memo: "arsune:fund:11111111-2222-4333-8444-555555555555",
    tx_id: "dry-abc",
    explorer_url: null,
    settlement_mode: "dry",
    status: "completed",
    created_at: "2026-11-09T10:05:00Z",
  };
}

function reward(claim_id: string, amount: number) {
  return {
    ...fundRow(amount),
    id: `r-${claim_id}`,
    claim_id,
    kind: "reward_out",
    account: HUNTER,
  };
}

/** An open, funded bounty: the race can start. */
function makeReady(api: Api) {
  Object.assign(api.bounty, { status: "open", budget_amount: 10_000_000 });
  api.bounty.ledger.push(fundRow(10_000_000));
}

async function mockApi(
  page: Page,
  mode: Mode,
  url = "http://127.0.0.1:8541/shop/",
): Promise<Api> {
  const state: Api = {
    bounty: makeBounty(mode, url),
    calls: [],
    funding: [],
    readsUntilChecked: 0,
    host: null,
    swarm: null,
  };
  const opts = options(mode);
  await page.route("**/api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const body = req.postData() ? JSON.parse(req.postData()!) : null;
    state.calls.push({ method: req.method(), path, body });
    const b = state.bounty;
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (path === "/api/funding/options") return json(opts);
    if (path === "/api/bounties" && req.method() === "GET")
      return json({ items: [b], settlement_mode: mode });
    if (path === "/api/bounties" && req.method() === "POST") {
      Object.assign(b, {
        url: body.url,
        owner_account: body.owner_account,
        reward_amount: body.reward_amount,
        bond_amount: body.bond_amount,
        fee_amount: Math.floor(body.reward_amount / 10),
        budget_target: body.budget,
      });
      return json(b, 201);
    }
    if (path === `/api/bounties/${ID}`) {
      finishSiteCheck(state);
      return json(b);
    }
    if (path === `/api/bounties/${ID}/demo-proof`)
      return json({ published: true, proof_url: b.proof_url });
    if (path === `/api/bounties/${ID}/verify`) {
      if (state.verifyError) return json({ detail: state.verifyError }, 409);
      checking(state);
      return json(b, 202);
    }
    if (path === `/api/bounties/${ID}/site-check` && req.method() === "POST") {
      checking(state);
      return json({ bounty_id: ID, site_check: b.site_check }, 202);
    }
    if (path === `/api/bounties/${ID}/fund-simulated`) {
      b.budget_amount += body.amount;
      // Arsune reopens a bounty that closed only because its budget ran out.
      const perBug = b.reward_amount + b.fee_amount;
      if (b.closed_reason === "budget_spent" && b.budget_amount >= perBug)
        Object.assign(b, { status: "open", closed_reason: null });
      b.ledger.push(fundRow(body.amount));
      return json({
        simulated: true,
        sender: "a".repeat(64),
        amount: body.amount,
        bounty: b,
      });
    }
    if (path === `/api/bounties/${ID}/funding-address`) {
      if (state.quoteError) return json({ detail: state.quoteError }, 422);
      const request = {
        id: "r1",
        status: "pending_deposit",
        final: false,
        oneclick_status: null,
        amount: body.amount,
        origin_asset: body.origin_asset,
        origin_symbol: "USDC",
        origin_chain: "base",
        origin_decimals: 6,
        deposit_address: "0x76b4c56085ED136a8744D52bE956396624a730E8",
        deposit_memo: null,
        amount_in: "9100000",
        amount_in_formatted: "9.1",
        min_amount_in: "9010000",
        min_amount_in_formatted: "9.01",
        refund_to: body.refund_to,
        deadline: new Date(Date.now() + 60 * 60_000).toISOString(),
        address_closes_at: new Date(Date.now() + 3 * 86_400_000).toISOString(),
        time_estimate: 40,
        delivered_amount: null,
        note: null,
        checked_at: null,
        created_at: new Date().toISOString(),
      };
      state.funding.unshift(request);
      return json(request, 201);
    }
    if (path === `/api/bounties/${ID}/funding`)
      return json({
        bounty_id: ID,
        bounty_status: b.status,
        budget_amount: b.budget_amount,
        budget_target: b.budget_target,
        settlement_mode: mode,
        requests: state.funding,
        status_error: null,
      });
    return json({ detail: "Not found" }, 404);
  });
  await page.route("**/swarm-api/**", async (route: Route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const body = req.postData() ? JSON.parse(req.postData()!) : null;
    state.calls.push({ method: req.method(), path, body });
    const h = state.host;
    // The Next proxy answers like this when the race host is down.
    if (!h)
      return route.fulfill({ status: 500, body: "Internal Server Error" });
    if (path === "/swarm-api/host") {
      // A stopped race ends in the background: the host stays busy for a moment.
      if (state.stopping !== undefined && --state.stopping < 0) {
        h.busy = false;
        if (state.swarm) state.swarm.finished = true;
        state.stopping = undefined;
      }
      return route.fulfill({ json: h });
    }
    if (path === "/swarm-api/races" && req.method() === "POST") {
      const keys = Object.keys(body ?? {})
        .sort()
        .join(",");
      if (
        req.headers()["content-type"] !== "application/json" ||
        keys !== "ai_agents,bounty_id"
      )
        return route.fulfill({
          status: 400,
          json: { detail: "Send JSON with bounty_id and ai_agents only." },
        });
      if (state.raceRefusal)
        return route.fulfill({
          status: state.raceRefusal.status,
          json: { detail: state.raceRefusal.detail },
        });
      Object.assign(h, { busy: true, bounty_id: body.bounty_id, run_id: "r9" });
      state.swarm = {
        run_id: "r9",
        mode: "simulation",
        bounty_id: body.bounty_id,
        started_at: "2026-11-09T11:00:00Z",
        finished: false,
        agents: Array.from({ length: 15 }, (_, i) => ({
          id: `hunter-${String(i + 1).padStart(3, "0")}`,
          account: `h${i}.near`,
          role: "hunter",
          strategy: "links",
          state: "searching",
          balance: 300_000,
          claims: 0,
          proven: 0,
          earned: 0,
          bonds_lost: 0,
        })),
        tips: [],
        log: [],
        site_check: { state: "passed", reason: null },
      };
      return route.fulfill({
        status: 202,
        json: { run_id: "r9", bounty_id: body.bounty_id },
      });
    }
    if (path === "/swarm-api/races/stop" && req.method() === "POST") {
      if (!h.busy || state.stopping !== undefined)
        return route.fulfill({
          status: 409,
          json: { detail: "No race is running." },
        });
      state.stopping = 2;
      return route.fulfill({
        status: 202,
        json: { run_id: h.run_id, bounty_id: h.bounty_id },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.route("**/swarm-state", (route) =>
    state.swarm
      ? route.fulfill({ json: state.swarm })
      : route.fulfill({ status: 500, body: "down" }),
  );
  return state;
}

const START_URL = /^http:\/\/[^/]+\/$/;

test("the header has three links and no tools menu", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, "dry");
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Tasks" });
  await expect(nav.getByRole("link")).toHaveText([
    "Start a race",
    "Your races",
    "Live race",
  ]);
  for (const [label, href] of [
    ["Start a race", "/"],
    ["Your races", "/races"],
    ["Live race", "/swarm"],
  ])
    await expect(
      nav.getByRole("link", { name: label, exact: true }),
    ).toHaveAttribute("href", href);
  await expect(nav.locator("[aria-current='page']")).toHaveText("Start a race");
  await expect(page.getByText("More tools")).toHaveCount(0);
  for (const link of await nav.getByRole("link").all()) {
    const box = (await link.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("old addresses lead to the new pages", async ({ page }) => {
  await mockApi(page, "dry");
  await page.goto("/bounties/new");
  await expect(page).toHaveURL(START_URL);
  await expect(
    page.getByRole("heading", { name: "Start a bug race" }),
  ).toBeVisible();
  await page.goto("/?job=saved-report");
  await expect(page).toHaveURL(/\/checks\?job=saved-report$/);
});

test("start page: validates, creates the race and opens its page", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  api.host = host();
  await page.goto("/");
  await expect(page.getByTestId("sim-label")).toHaveText(
    "Simulation · no real money",
  );
  // The start page has no agent choice; step 4 offers it when it is a real choice.
  await expect(page.getByRole("radio")).toHaveCount(0);
  // The problem first, then the promise; "bond" is defined once, here.
  await expect(page.locator("h1")).toHaveText(
    "AI builds sites fast. Bugs ship with them.Let agents race to find them. Pay only for proof.",
  );
  await expect(page.locator(".lead")).toContainText(
    "Each claim locks a bond, a small deposit.",
  );
  await expect(page.locator(".start-steps strong")).toHaveText([
    "Fund a bounty pool",
    "Agents race",
    "Arsune checks each claim",
  ]);
  await expect(page.getByTestId("start-outcome")).toHaveText(
    "You get a list of proven bugs, each with evidence that anyone can check again.",
  );
  await page.getByRole("button", { name: "Start a bug race" }).click();
  await expect(page.getByText("Enter the full address")).toBeVisible();
  await expect(page.getByText("Enter a NEAR account ID")).toBeVisible();
  expect(api.calls.some((c) => c.path === "/api/bounties")).toBe(false);

  // Reward, bond and budget wait behind "Advanced" with working defaults.
  await expect(page.getByLabel("Budget")).toBeHidden();
  await expect(page.getByTestId("start-terms")).toHaveText(
    "1.00 USDC per proven bug · at most 10.00 USDC in total",
  );
  await page.getByText("Advanced: reward, bond, budget").click();
  await page.getByLabel("Budget").fill("0.5");
  await expect(page.getByTestId("preview-bugs")).toHaveText("0 proven bugs");
  await expect(
    page.getByText("The budget must cover at least one reward and its fee."),
  ).toBeVisible();
  await page.getByLabel("Budget").fill("12.5");
  await expect(page.getByTestId("preview-fee")).toHaveText("0.10 USDC");
  await expect(page.getByTestId("preview-bugs")).toHaveText("11 proven bugs");

  await expect(page.getByTestId("demo-hint")).toHaveText(
    "A small shop with known bugs. Arsune sets the proof for you.",
  );
  await page.getByRole("button", { name: "Try the demo shop" }).click();
  await expect(page.getByLabel("Site URL")).toHaveValue(
    "http://127.0.0.1:8541/shop/",
  );
  await expect(page.getByLabel("Your NEAR account")).toHaveValue(
    "demo-owner.near",
  );
  await page.getByLabel("Base bond per claim").fill("0.25");
  await page.getByRole("button", { name: "Start a bug race" }).click();
  await expect(page).toHaveURL(new RegExp(`/bounties/${ID}$`));
  const create = api.calls.find(
    (c) => c.path === "/api/bounties" && c.method === "POST",
  );
  expect(create?.body).toEqual({
    url: "http://127.0.0.1:8541/shop/",
    owner_account: "demo-owner.near",
    reward_amount: 1_000_000,
    bond_amount: 250_000,
    budget: 12_500_000,
  });
  await expect(page.getByTestId("step-1-status")).toHaveText(
    "Waiting for proof",
  );
  await expect(
    page.getByTestId("step-4").getByLabel("Standard agents (free)"),
  ).toBeChecked();
  await expect(page.getByTestId("step-4-status")).toHaveText("After funding");
});

test("an invalid advanced field opens the advanced section", async ({
  page,
}) => {
  await mockApi(page, "dry");
  await page.goto("/");
  await page.getByLabel("Site URL").fill("https://shop.example.com/");
  await page.getByLabel("Your NEAR account").fill("me.near");
  await page.getByText("Advanced: reward, bond, budget").click();
  await page.getByLabel("Reward per proven bug").fill("abc");
  await page.getByText("Advanced: reward, bond, budget").click();
  await expect(page.getByLabel("Reward per proven bug")).toBeHidden();
  await page.getByRole("button", { name: "Start a bug race" }).click();
  await expect(page.getByLabel("Reward per proven bug")).toBeVisible();
  await expect(page.locator("#reward-error")).toHaveText(
    "Enter an amount above 0, with at most 6 decimals.",
  );
});

test("start page shows the API error", async ({ page }) => {
  await mockApi(page, "dry");
  await page.route("**/api/bounties", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "Private network addresses are not permitted." },
    }),
  );
  await page.goto("/");
  await page.getByLabel("Site URL").fill("http://10.0.0.5/");
  await page.getByLabel("Your NEAR account").fill("me.near");
  await page.getByRole("button", { name: "Start a bug race" }).click();
  await expect(page.getByTestId("create-error")).toHaveText(
    "Private network addresses are not permitted.",
  );
  await expect(page).toHaveURL(START_URL);
});

test("dry flow: demo proof, site check, one-click funding, then ready to race", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  api.host = host();
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("step-1-status")).toHaveText(
    "Waiting for proof",
  );
  await expect(page.getByTestId("step-2-status")).toHaveText("After the proof");
  await expect(page.getByTestId("step-3-status")).toHaveText(
    "After your site loads",
  );
  await expect(page.getByTestId("step-4-status")).toHaveText("After funding");
  await expect(page.getByTestId("race-locked")).toHaveText(
    "First prove that you own the site. Arsune then loads it.",
  );
  await expect(
    page.getByRole("button", { name: "Start the race" }),
  ).toBeDisabled();
  await expect(page.getByTestId("proof-token")).toHaveText(TOKEN);
  await expect(page.getByTestId("proof-url")).toHaveText(
    "http://127.0.0.1:8541/.well-known/arsune-bounty.txt",
  );

  await page
    .getByRole("button", { name: "Demo site: set proof automatically" })
    .click();
  // The proof is read; Arsune then loads the site before the bounty opens.
  await expect(page.getByTestId("site-check")).toHaveAttribute(
    "data-state",
    "checking",
  );
  await expect(page.getByTestId("step-1-status")).toHaveText("Done");
  await expect(page.getByTestId("step-2-status")).toHaveText(
    "Loading your site…",
  );
  await expect(page.getByTestId("bounty-status")).toHaveText(
    "Waiting for proof",
  );
  await expect(page.getByTestId("proof-done")).toContainText(
    "Ownership proven",
  );
  await expect(page.getByTestId("site-check")).toContainText(
    "completely at 1280 and 390 pixels wide",
  );
  await expect(page.getByTestId("step-2-status")).toHaveText("Done");
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  const order = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(order).toEqual([
    `/api/bounties/${ID}/demo-proof`,
    `/api/bounties/${ID}/verify`,
  ]);

  await expect(page.getByTestId("step-3-status")).toHaveText("Not funded");
  await expect(page.getByTestId("funded-total")).toHaveText(
    "Not funded yet. The planned budget is 10.00 USDC.",
  );
  // No real money moves in a simulation: simulated money is the only way to fund.
  await expect(page.getByText("Other ways to fund")).toHaveCount(0);
  await expect(page.getByTestId("crosschain-option")).toHaveCount(0);
  await expect(page.getByTestId("direct-option")).toHaveCount(0);
  await expect(page.getByLabel("Amount to add")).toHaveValue("10.00");
  await page.getByLabel("Amount to add").fill("abc");
  await expect(
    page.getByRole("button", { name: "Add simulated money" }),
  ).toBeDisabled();
  await page.getByLabel("Amount to add").fill("10");
  await page.getByRole("button", { name: "Add simulated money" }).click();
  await expect(page.getByTestId("fund-done")).toHaveText(
    "Added 10.00 USDC of simulated money.",
  );
  await expect(page.getByTestId("funded-total")).toHaveText(
    "Funded 10.00 USDC · pays for 9 bugs · 10.00 USDC left",
  );
  // A simulated payment row has no link and no extra tag: the header says it all.
  await expect(page.getByTestId("fund-rows")).toHaveText(
    "Money added10.00 USDC",
  );
  await expect(page.getByTestId("fund-rows").getByRole("link")).toHaveCount(0);
  await expect(page.getByTestId("sim-label")).toHaveCount(1);
  await expect(page.getByTestId("step-3-status")).toHaveText("Funded");
  const fund = api.calls.find((c) => c.path.endsWith("/fund-simulated"));
  expect(fund?.body).toEqual({ amount: 10_000_000 });

  await expect(page.getByTestId("step-4-status")).toHaveText("Ready to start");
  await expect(
    page.getByRole("button", { name: "Start the race" }),
  ).toBeEnabled();
  await expect(page.getByTestId("race-hint")).toHaveText(
    "15 agents will search 127.0.0.1:8541/shop/. You pay only for proven bugs.",
  );
  const strip = page.getByTestId("step-strip");
  await expect(strip).toContainText("Fund the raceFunded");
  await expect(strip).toContainText("Start the raceReady to start");
});

test("a top-up reopens a bounty whose budget ran out", async ({ page }) => {
  const api = await mockApi(page, "dry");
  Object.assign(api.bounty, {
    status: "closed",
    closed_reason: "budget_spent",
    budget_amount: 400_000,
  });
  api.bounty.ledger.push(fundRow(10_000_000));
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("bounty-status")).toHaveText("Budget spent");
  await expect(page.getByTestId("step-3-status")).toHaveText("Budget spent");
  await expect(page.getByTestId("race-locked")).toHaveText(
    "The budget is spent. Add money in step 3 to open the bounty again.",
  );
  await expect(page.getByTestId("reopen-hint")).toHaveText(
    "The budget ran out, so the bounty is closed. Add at least 0.70 USDC to open it again.",
  );
  await expect(page.getByTestId("funded-total")).toHaveText(
    "Funded 10.00 USDC · pays for 0 more bugs · 0.40 USDC left",
  );
  await page.getByLabel("Amount to add").fill("0.7");
  await page.getByRole("button", { name: "Add simulated money" }).click();
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  await expect(page.getByTestId("reopen-hint")).toHaveCount(0);
  await expect(page.getByTestId("funded-total")).toContainText(
    "pays for 1 more bug ·",
  );
});

test("manual proof gives exact instructions and shows the checker's refusal", async ({
  page,
}) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  api.verifyError = "The proof file returned HTTP 404.";
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("proof-url")).toHaveText(
    "https://shop.example.com/.well-known/arsune-bounty.txt",
  );
  await expect(
    page.getByRole("button", { name: "Demo site: set proof automatically" }),
  ).toHaveCount(0);
  await expect(page.getByText(`printf '%s\\n' '${TOKEN}'`)).toBeVisible();
  await page.getByRole("button", { name: "Check ownership" }).click();
  await expect(page.getByTestId("proof-error")).toHaveText(
    "The proof file returned HTTP 404.",
  );
  await expect(page.getByTestId("step-1-status")).toHaveText(
    "Waiting for proof",
  );

  api.verifyError = undefined;
  await page.getByRole("button", { name: "Check ownership" }).click();
  // A done step folds to one line; its message waits behind "Show".
  await expect(page.getByTestId("step-1-status")).toHaveText("Done");
  await expect(page.getByTestId("proof-done")).toContainText(
    "Ownership proven",
  );
});

test("a site that Arsune cannot load completely does not open, and the owner checks again", async ({
  page,
}) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  api.siteProblem = UNLOADED;
  await page.goto(`/bounties/${ID}`);
  await page.getByRole("button", { name: "Check ownership" }).click();
  await expect(page.getByTestId("site-check")).toHaveAttribute(
    "data-state",
    "failed",
  );
  await expect(page.getByTestId("site-check")).toContainText(UNLOADED);
  await expect(page.getByTestId("step-1-status")).toHaveText("Done");
  await expect(page.getByTestId("step-2-status")).toHaveText(
    "Could not load your site",
  );
  await expect(page.getByTestId("bounty-status")).toHaveText(
    "Waiting for proof",
  );
  await expect(page.getByTestId("step-3-status")).toHaveText(
    "After your site loads",
  );

  api.siteProblem = undefined;
  await page.getByRole("button", { name: "Check again" }).click();
  await expect(page.getByTestId("step-2-status")).toHaveText("Done");
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  const posts = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(posts).toEqual([
    `/api/bounties/${ID}/verify`,
    `/api/bounties/${ID}/verify`,
  ]);
});

test("an open bounty checks its site again on request", async ({ page }) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  makeReady(api);
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("step-2-status")).toHaveText(
    "Checked when a race starts",
  );
  await page.getByRole("button", { name: "Check now" }).click();
  await expect(page.getByTestId("step-2-status")).toHaveText("Done");
  const posts = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(posts).toEqual([`/api/bounties/${ID}/site-check`]);
});

test("a paused bounty shows Arsune's reason and resumes after a passing site check", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  const reason =
    "Arsune cannot check this site: the last 5 claims were unverified because Arsune could " +
    "not load their pages completely. Last claim: Script https://cdn.example/app.js.";
  Object.assign(api.bounty, {
    status: "paused",
    paused_reason: reason,
    budget_amount: 10_000_000,
  });
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("bounty-status")).toHaveText("Paused");
  await expect(page.getByTestId("paused-banner")).toContainText(
    "takes no new claims",
  );
  await expect(page.getByTestId("paused-banner")).toContainText(reason);
  await page.getByRole("button", { name: "Check the site and resume" }).click();
  await expect(
    page.getByTestId("step-2").getByTestId("site-check"),
  ).toHaveAttribute("data-state", "checking");
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  await expect(page.getByTestId("paused-banner")).toHaveCount(0);
  const posts = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(posts).toEqual([`/api/bounties/${ID}/verify`]);
});

test("live mode: 1Click deposit address with copy and live status", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const api = await mockApi(page, "near_intents", "https://shop.example.com/");
  api.bounty.status = "open";
  await page.goto(`/bounties/${ID}`);
  await expect(
    page.getByText("Real settlement via NEAR Intents").first(),
  ).toBeVisible();
  await expect(page.getByTestId("sim-label")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Add simulated money" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("fund-memo")).toContainText(
    /^arsune:fund:[0-9a-f-]{36}$/,
  );

  const option = page.getByTestId("crosschain-option");
  await expect(option.getByLabel("Pay with")).toHaveValue(/base-0x8335/);
  await option.getByRole("button", { name: "Get deposit address" }).click();
  await expect(page.getByTestId("crosschain-error")).toHaveText(
    "Enter your Base address for refunds.",
  );
  api.quoteError =
    "1Click refused the quote: refundTo is not a valid base address";
  await option.getByLabel(/Your refund address on Base/).fill("0xnope");
  await option.getByRole("button", { name: "Get deposit address" }).click();
  await expect(page.getByTestId("crosschain-error")).toHaveText(api.quoteError);

  api.quoteError = undefined;
  await page.getByLabel("Amount to add").fill("9");
  await option
    .getByLabel(/Your refund address on Base/)
    .fill("0x2527D02599Ba641c19FEa793cD0F167589a0f10D");
  await option.getByRole("button", { name: "Get deposit address" }).click();
  const deposit = page.getByTestId("deposit");
  await expect(deposit.getByTestId("deposit-address")).toHaveText(
    "0x76b4c56085ED136a8744D52bE956396624a730E8",
  );
  await expect(deposit.getByTestId("deposit-amount")).toHaveText("9.1 USDC");
  await expect(deposit.getByTestId("deposit-status")).toHaveText(
    "Waiting for your deposit",
  );
  await expect(deposit.getByTestId("deposit-deadline")).toContainText(
    /in (59|60) min/,
  );
  expect(
    api.calls.filter((c) => c.path.endsWith("/funding-address")).at(-1)?.body,
  ).toEqual({
    amount: 9_000_000,
    origin_asset:
      "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near",
    refund_to: "0x2527D02599Ba641c19FEa793cD0F167589a0f10D",
  });

  await deposit.getByRole("button", { name: "Copy deposit address" }).click();
  await expect(
    deposit.getByRole("button", { name: "Copy deposit address" }),
  ).toHaveText("Copied");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
    "0x76b4c56085ED136a8744D52bE956396624a730E8",
  );

  const request = api.funding[0];
  Object.assign(request, {
    status: "processing",
    oneclick_status: "PROCESSING",
  });
  await expect(deposit.getByTestId("deposit-status")).toHaveText(
    "Swapping into USDC",
  );
  Object.assign(request, {
    status: "credited",
    final: true,
    oneclick_status: "SUCCESS",
    delivered_amount: 9_000_000,
  });
  api.bounty.budget_amount = 9_000_000;
  await expect(deposit.getByTestId("deposit-status")).toHaveText(
    "Credited to the bounty",
  );
  await expect(deposit.getByTestId("deposit-credited")).toHaveText(
    "Credited 9.00 USDC to the bounty.",
  );
  await expect(deposit.getByTestId("deposit-address")).toHaveCount(0);
  await expect(page.getByTestId("funded-total")).toHaveText(
    "Funded 9.00 USDC · pays for 8 bugs · 9.00 USDC left",
  );
});

test("an unknown bounty says so", async ({ page }) => {
  await mockApi(page, "dry");
  await page.goto("/bounties/00000000-0000-4000-8000-000000000000");
  await expect(page.getByTestId("load-error")).toContainText("Not found");
  await expect(
    page.getByRole("link", { name: "Start a new race" }),
  ).toBeVisible();
});

test("a failed signed transfer gets a new fund_id and memo, a 422 keeps them", async ({
  page,
}) => {
  const api = await mockApi(page, "escrow", "https://shop.example.com/");
  api.bounty.status = "open";
  const replies = [504, 422];
  await page.route(`**/api/bounties/${ID}/fund`, (route) => {
    const status = replies.shift() ?? 200;
    return route.fulfill({ status, json: { detail: "no" } });
  });
  await page.goto(`/bounties/${ID}`);
  await page.getByLabel("Signer account").fill("alice.near");
  await page.getByLabel("Signed transfer (JSON)").fill("{}");
  const memo = page.getByTestId("fund-memo");
  const first = await memo.innerText();
  await page.getByRole("button", { name: "Submit signed transfer" }).click();
  await expect(
    page.getByRole("button", { name: "Submit signed transfer" }),
  ).toBeEnabled();
  const second = await memo.innerText();
  expect(second).not.toBe(first);
  await page.getByRole("button", { name: "Submit signed transfer" }).click();
  await expect(
    page.getByRole("button", { name: "Submit signed transfer" }),
  ).toBeEnabled();
  await expect(memo).toHaveText(second);
});

// Claims in the shape GET /api/bounties/{id} returns them.
function claim(over: Record<string, unknown>) {
  const id = over.id as string;
  return {
    hunter_account: HUNTER,
    scout: null,
    fingerprint: null,
    evidence_hash: HASH,
    evidence_url: `/api/claims/${id}/evidence`,
    settlement_note: null,
    created_at: "2026-11-09T11:00:05Z",
    ...over,
  };
}

const PROVEN = "13c04072-3a89-428c-9b19-60cde745cf87";
const CLAIMS = [
  claim({
    id: PROVEN,
    claim_type: "horizontal_overflow",
    urls: ["https://shop.example.com/roi-calculator"],
    details: { url: "https://shop.example.com/roi-calculator" },
    fingerprint: FINGERPRINT,
    status: "paid",
    outcome: "proven",
    checker_reason:
      "Fresh measurement: content width 601 pixels; viewport width 390 pixels.",
    settlement_note: "Reward and bond paid.",
  }),
  claim({
    id: "9f6b53d4-e22b-47b5-9b9e-5c5cd95dcba3",
    claim_type: "horizontal_overflow",
    urls: ["https://shop.example.com/roi-calculator"],
    details: { url: "https://shop.example.com/roi-calculator" },
    fingerprint: FINGERPRINT,
    status: "duplicate",
    outcome: "duplicate",
    checker_reason: `Claim ${PROVEN} already proved this fault. No browser check was needed.`,
    settlement_note: "Bond refunded.",
  }),
  claim({
    id: "611d0ddd-a5d0-484f-adbf-2a0d4b785ff0",
    claim_type: "broken_link",
    urls: [
      "https://shop.example.com/",
      "https://shop.example.com/roi-calculator",
    ],
    details: {
      source: "https://shop.example.com/",
      target: "https://shop.example.com/roi-calculator",
    },
    status: "rejected",
    outcome: "rejected",
    checker_reason:
      "A fresh source-page read found the link. A fresh target request returned HTTP 200.",
  }),
  claim({
    id: "0b5b2a37-55a9-4b39-8b0f-96f7a3e1c2d4",
    claim_type: "page_error",
    urls: ["https://shop.example.com/generador-enlace-whatsapp"],
    details: {
      page: "https://shop.example.com/generador-enlace-whatsapp",
      message: "syntaxerror: unexpected token '<'",
    },
    status: "paid",
    outcome: "proven",
    checker_reason:
      "A fresh page load raised an uncaught error: SyntaxError: Unexpected token '<'",
  }),
  claim({
    id: "8d3b5a4e-1f2c-4d6e-9a7b-0c1d2e3f4a5b",
    claim_type: "broken_image",
    urls: ["https://shop.example.com/"],
    details: {
      page: "https://shop.example.com/",
      image: "https://shop.example.com/img/hero.png",
    },
    status: "unverified",
    outcome: "unverified",
    checker_reason:
      "The image did not finish loading, or Arsune's limits blocked it.",
  }),
  claim({
    id: "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
    claim_type: "price_mismatch",
    urls: ["https://shop.example.com/a", "https://shop.example.com/b"],
    details: { key: "plan-pro" },
    status: "checking",
    outcome: null,
    checker_reason: null,
    evidence_hash: null,
    evidence_url: null,
  }),
];

test("dry mode shows one simulation label on each page, in its header", async ({
  page,
}) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  makeReady(api);
  api.bounty.claims = CLAIMS;
  api.bounty.ledger.push(reward(PROVEN, 900_000));
  for (const [path, head] of [
    ["/", ".panel-title"],
    ["/races", ".sw-meta"],
    ["/swarm", ".sw-meta"],
    [`/bounties/${ID}`, ".sw-meta"],
  ]) {
    await page.goto(path);
    const label = page.getByTestId("sim-label");
    await expect(label).toHaveText("Simulation · no real money");
    await expect(page.locator(head).getByTestId("sim-label")).toHaveCount(1);
    // No "Simulated" tags on payment rows, not even in closed parts.
    await expect(page.locator("body")).not.toContainText("Simulated");
    await expect(page.locator("body")).not.toContainText("no real money —");
  }
});

test("a done step shows one line, and its body waits behind Show", async ({
  page,
}) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  makeReady(api);
  api.bounty.site_check = {
    job_id: "j1",
    state: "passed",
    url: api.bounty.url,
    reason: `Arsune loaded ${api.bounty.url} completely at 1280 and 390 pixels wide.`,
    problems: [],
    note: null,
    checked_at: "2026-11-09T10:01:00Z",
    fresh_until: null,
  };
  await page.goto(`/bounties/${ID}`);
  for (const [n, title] of [
    [1, "Prove that you own the site"],
    [2, "Arsune loads your site"],
  ] as const) {
    await expect(page.getByTestId(`step-${n}-status`)).toHaveText("Done");
    const done = page.getByTestId(`step-${n}-done`);
    await expect(done).not.toHaveAttribute("open");
    const line = done.locator(":scope > summary");
    await expect(line).toContainText(title);
    await expect(line.locator(".bo-show")).toBeVisible();
    await expect(line.locator(".bo-hide")).toBeHidden();
    expect((await line.boundingBox())!.height).toBeLessThan(40);
  }
  await expect(page.getByTestId("proof-done")).toBeHidden();
  const again = page.getByRole("button", { name: "Check again" });
  await expect(again).toBeHidden();
  const line = page.getByTestId("step-2-done").locator(":scope > summary");
  await line.click();
  await expect(again).toBeVisible();
  await expect(line.locator(".bo-hide")).toBeVisible();
  await expect(line.locator(".bo-show")).toBeHidden();
  // Steps that still need the owner keep their bodies open.
  await expect(page.getByTestId("funded-total")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Start the race" }),
  ).toBeVisible();
});

test("results come first, in plain words; hashes and IDs stay in Details", async ({
  page,
  context,
  baseURL,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  makeReady(api);
  api.bounty.claims = CLAIMS;
  api.bounty.ledger.push(
    reward(PROVEN, 900_000),
    reward("0b5b2a37-55a9-4b39-8b0f-96f7a3e1c2d4", 900_000),
  );
  await page.goto(`/bounties/${ID}`);
  const results = page.getByTestId("results");
  await expect(results.getByTestId("claim-result")).toHaveCount(6);
  await expect(page.getByTestId("results-summary")).toHaveText(
    "6 claims · 2 proven · 1 false · 1 repeat · 1 not checked · 1 being checked",
  );
  // The outcome as sentences, above the list.
  await expect(page.getByTestId("result-story")).toHaveText(
    "Your race found 2 bugs. 2 are paid (1.80 USDC). " +
      "1 false claim cost you nothing: its bond went into your pool. " +
      "1 repeat got its bond back.",
  );
  // Proven bugs and claims being checked stay open; the other groups are closed.
  for (const g of ["proven", "pending"])
    await expect(
      page.getByTestId(`group-${g}`).getByTestId("claim-title").first(),
    ).toBeVisible();
  for (const g of ["false", "repeat", "unchecked"]) {
    const group = page.getByTestId(`group-${g}`);
    await expect(group).not.toHaveAttribute("open");
    await expect(group.locator(":scope > summary .bo-show")).toBeVisible();
    await expect(group.getByTestId("claim-title")).toBeHidden();
  }
  // Results sit above the steps.
  const top = (await results.boundingBox())!.y;
  expect(top).toBeLessThan((await page.getByTestId("step-1").boundingBox())!.y);

  const proven = page.getByTestId("group-proven");
  await expect(proven.getByRole("heading")).toHaveText("Proven bugs (paid) 2");
  await expect(proven.getByTestId("claim-title")).toHaveText([
    "Too wide on mobile (601 px on a 390 px screen)",
    "Script error on load: SyntaxError: Unexpected token '<'",
  ]);
  await expect(proven.getByTestId("claim-reason").first()).toHaveText(
    "Fresh measurement: content width 601 pixels; viewport width 390 pixels.",
  );
  await expect(proven.locator(".bo-result-page").first()).toHaveText(
    "/roi-calculator",
  );
  const falseClaims = page.getByTestId("group-false");
  await expect(falseClaims.getByRole("heading")).toHaveText(
    "False claims (bond lost) 1",
  );
  await falseClaims.locator(":scope > summary").click();
  await expect(falseClaims).toHaveAttribute("open");
  await expect(falseClaims.locator(":scope > summary .bo-hide")).toBeVisible();
  await expect(falseClaims.getByTestId("claim-title")).toBeVisible();
  await expect(falseClaims.getByTestId("claim-title")).toHaveText(
    "Broken link to /roi-calculator",
  );
  const repeats = page.getByTestId("group-repeat");
  await expect(repeats.getByRole("heading")).toHaveText(
    "Repeats (already found) 1",
  );
  // A repeat borrows the measurement of the claim that proved the bug.
  await expect(repeats.getByTestId("claim-title")).toHaveText(
    "Too wide on mobile (601 px on a 390 px screen)",
  );
  await expect(repeats.getByTestId("claim-reason")).toHaveText(
    "An earlier claim already proved this fault. No browser check was needed.",
  );
  await expect(
    page.getByTestId("group-unchecked").getByRole("heading"),
  ).toHaveText("Not checked (Arsune could not check) 1");
  await expect(
    page.getByTestId("group-unchecked").getByTestId("claim-title"),
  ).toHaveText("Image does not load: /img/hero.png");
  const pending = page.getByTestId("group-pending");
  await expect(pending.getByTestId("claim-title")).toHaveText(
    "Price differs between pages",
  );
  await expect(pending.getByTestId("claim-reason")).toHaveText(
    "Arsune is checking this claim in a fresh browser.",
  );
  await expect(pending.getByTestId("evidence-link")).toHaveCount(0);

  const shown = await results.innerText();
  for (const hidden of [HASH, HASH.slice(0, 12), FINGERPRINT, PROVEN, HUNTER])
    expect(shown).not.toContain(hidden);
  expect(shown).not.toContain("sha256");

  const row = proven.getByTestId("claim-result").first();
  await expect(row.getByTestId("evidence-link")).toHaveAttribute(
    "href",
    `/api/claims/${PROVEN}/evidence`,
  );
  await row.getByText("Details").click();
  const details = row.getByTestId("claim-details");
  await expect(details).toContainText(PROVEN);
  await expect(details).toContainText(HUNTER);
  await expect(details).toContainText(FINGERPRINT);
  await expect(details).toContainText("Bond0.10 USDC");
  await expect(details).toContainText("Reward and bond paid.");
  const hash = details.getByTestId("evidence-hash");
  await expect(hash).toHaveText(`sha256 ${HASH.slice(0, 12)}…`);
  await expect(hash).toHaveAttribute("title", HASH);
  await details.getByRole("button", { name: "Copy evidence hash" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(HASH);

  await page.getByText("Technical details").click();
  await expect(page.getByTestId("verify-hint")).toContainText(
    `uv run python -m app.verify ${new URL(baseURL!).origin} <claim_id>`,
  );
  await expect(page.getByTestId("technical-details")).toContainText(ID);
});

test("step 4 starts the race, shows this race live, and stops it", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  makeReady(api);
  api.host = host();
  await page.goto(`/bounties/${ID}`);
  const step = page.getByTestId("step-4");
  await expect(page.getByTestId("step-4-status")).toHaveText("Ready to start");
  // The race host has AI agents, so the owner gets a real choice.
  await expect(step.getByRole("radio")).toHaveCount(2);
  await expect(step.getByLabel("Standard agents (free)")).toBeChecked();
  await expect(step.locator(".mode-options label")).toHaveText([
    "Standard agents (free)12 hunters and 3 scouts with fixed rules.",
    "Standard + AI agentsAI agents also search. They use model credits.",
  ]);
  await expect(page.getByTestId("race-hint")).toHaveText(
    "15 agents will search shop.example.com/. You pay only for proven bugs.",
  );
  await step.getByLabel("Standard + AI agents").check();
  await expect(page.getByTestId("race-hint")).toHaveText(
    "19 agents will search shop.example.com/. You pay only for proven bugs.",
  );
  await page.getByRole("button", { name: "Start the race" }).click();
  const start = api.calls.find((c) => c.path === "/swarm-api/races");
  expect(start?.body).toEqual({ bounty_id: ID, ai_agents: true });

  await expect(page.getByTestId("step-4-status")).toHaveText("Running");
  const live = page.getByTestId("live-race");
  await expect(live).toContainText("The race is running.");
  await expect(live.getByTestId("race-line")).toBeVisible();
  await expect(live.getByTestId("line-agents")).toHaveText("15");
  await expect(live.getByTestId("line-claims")).toHaveText("0");
  await expect(live.getByTestId("line-see-all")).toHaveAttribute(
    "href",
    `/swarm?bounty=${ID}`,
  );
  await expect(page.getByTestId("live-stream")).toContainText(
    "No claims yet. The agents are searching.",
  );
  await expect(page.getByTestId("board-link")).toHaveAttribute(
    "href",
    `/swarm?bounty=${ID}`,
  );

  // Claims of this race arrive; an older claim of the bounty does not count.
  api.bounty.claims = [
    { ...CLAIMS[2], created_at: "2026-11-09T10:30:00Z" },
    CLAIMS[0],
    CLAIMS[1],
  ];
  api.bounty.ledger.push(reward(PROVEN, 900_000), reward("older", 5_000_000));
  await expect(live.getByTestId("line-claims")).toHaveText("2");
  await expect(live.getByTestId("line-paid")).toHaveText("1");
  await expect(live.getByTestId("line-repeat")).toHaveText("1");
  await expect(live.getByTestId("line-lost")).toHaveText("0");
  await expect(live.getByTestId("line-see-all")).toHaveText(
    "See all 2 claims →",
  );
  // The money of this race only: the older claim's reward is not counted.
  await expect(live.getByTestId("line-paid-amount")).toHaveText("+0.90 USDC");
  await expect(live.getByTestId("line-latest")).toHaveText(
    'an unnamed agent proved "Too wide on mobile (601 px on a 390 px screen)" on /roi-calculator → paid +0.90 USDC',
  );
  expect(await live.innerText()).not.toMatch(/[0-9a-f]{64}/);
  const shot = testInfo.outputPath("owner-live-1440.png");
  await live.screenshot({ path: shot });
  console.log("SCREENSHOT", shot);
  await expect(page.getByTestId("live-stream").locator("li")).toHaveText([
    "↺/roi-calculatorToo wide on mobile (601 px on a 390 px screen)Repeat",
    "✓/roi-calculatorToo wide on mobile (601 px on a 390 px screen)Proven, paid",
  ]);
  expect(await live.innerText()).not.toContain(HASH.slice(0, 12));

  // The host answers 202 at once and stops the race in the background.
  await page.getByRole("button", { name: "Stop the race" }).click();
  await expect(page.getByRole("button", { name: "Stopping…" })).toBeDisabled();
  await expect(page.getByTestId("step-4-status")).toHaveText("Finished");
  const stop = api.calls.filter((c) => c.path === "/swarm-api/races/stop");
  expect(stop).toEqual([
    { method: "POST", path: "/swarm-api/races/stop", body: null },
  ]);
  await expect(page.getByTestId("race-finished")).toHaveText(
    "The last race ended. Its results are at the top of this page.",
  );
  await expect(
    page.getByRole("button", { name: "Start a new race" }),
  ).toBeEnabled();
});

test("a race host that refuses the start shows its reason", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  makeReady(api);
  api.host = host();
  api.raceRefusal = {
    status: 409,
    detail: "A race is running already. Start this race when it ends.",
  };
  await page.goto(`/bounties/${ID}`);
  await page.getByRole("button", { name: "Start the race" }).click();
  await expect(page.getByTestId("race-error")).toHaveText(
    "A race is running already. Start this race when it ends.",
  );
  await expect(page.getByTestId("step-4-status")).toHaveText("Ready to start");
  for (const [status, detail] of [
    [400, "AI agents are not configured on this race host."],
    [404, `Arsune has no bounty ${ID}.`],
    [502, "Arsune's API did not answer."],
  ] as const) {
    api.raceRefusal = { status, detail };
    await page.getByRole("button", { name: "Start the race" }).click();
    await expect(page.getByTestId("race-error")).toHaveText(detail);
  }
  // Every start sent JSON with exactly the two contract fields.
  const starts = api.calls.filter((c) => c.path === "/swarm-api/races");
  expect(starts).toHaveLength(4);
  for (const s of starts)
    expect(s.body).toEqual({ bounty_id: ID, ai_agents: false });
});

test("without AI agents on the race host, step 4 shows no choice and no reason", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  makeReady(api);
  api.host = host({ ai_available: false, ai_model: null });
  await page.goto(`/bounties/${ID}`);
  const step = page.getByTestId("step-4");
  await expect(page.getByTestId("step-4-status")).toHaveText("Ready to start");
  await expect(page.getByTestId("race-hint")).toHaveText(
    "15 agents will search 127.0.0.1:8541/shop/. You pay only for proven bugs.",
  );
  await expect(step.getByRole("radio")).toHaveCount(0);
  await expect(step).not.toContainText("AI");
  await expect(step).not.toContainText("model");
  await page.getByRole("button", { name: "Start the race" }).click();
  const start = api.calls.find((c) => c.path === "/swarm-api/races");
  expect(start?.body).toEqual({ bounty_id: ID, ai_agents: false });
});

test("with the race host down, step 4 says so and cannot start", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  makeReady(api);
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("step-4-status")).toHaveText(
    "Race host offline",
  );
  await expect(page.getByTestId("host-down")).toHaveText(
    "The race host is not running.",
  );
  await expect(
    page.getByRole("button", { name: "Start the race" }),
  ).toBeDisabled();
  await expect(page.getByTestId("step-4").getByRole("radio")).toHaveCount(0);
  expect(api.calls.some((c) => c.path === "/swarm-api/races")).toBe(false);
});

test("while another bounty races, step 4 waits for it", async ({ page }) => {
  const api = await mockApi(page, "dry");
  makeReady(api);
  api.host = host({ busy: true, bounty_id: "other-bounty", run_id: "r1" });
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("step-4-status")).toHaveText(
    "Another race runs",
  );
  await expect(page.getByTestId("host-busy")).toContainText(
    "Another race is running now. You can start this race when it ends.",
  );
  await expect(
    page
      .getByTestId("host-busy")
      .getByRole("link", { name: "Watch that race" }),
  ).toHaveAttribute("href", "/swarm?bounty=other-bounty");
  await expect(
    page.getByRole("button", { name: "Start the race" }),
  ).toBeDisabled();
});

test("escrow mode: a signed deposit to the escrow contract names the bounty", async ({
  page,
}) => {
  const api = await mockApi(page, "escrow", "https://shop.example.com/");
  api.bounty.status = "open";
  await page.goto(`/bounties/${ID}`);
  await expect(
    page.getByText("Settlement by the escrow contract").first(),
  ).toBeVisible();
  await expect(page.getByText("Real settlement via NEAR Intents")).toHaveCount(
    0,
  );
  await expect(page.getByTestId("sim-label")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Add simulated money" }),
  ).toHaveCount(0);
  await expect(page.getByTestId("crosschain-unavailable")).toContainText(
    "escrow mode",
  );
  const direct = page.getByTestId("direct-option");
  await expect(direct).toContainText(`token to ${ESCROW}`);
  await expect(page.getByTestId("fund-msg")).toHaveText(`{"fund":"${ID}"}`);
  await expect(direct).toContainText("app.settlement.escrow.sign_deposit");
  await expect(direct).toContainText(`"msg":"{\\"fund\\":\\"${ID}\\"}"`);
  // Escrow mode does not poll 1Click deposit addresses.
  expect(api.calls.some((c) => c.path.endsWith("/funding"))).toBe(false);
});
