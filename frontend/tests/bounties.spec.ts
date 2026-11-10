import { expect, Page, Route, test } from "@playwright/test";

const ID = "6f1c9a52-8c1e-4d39-9a43-2b7f0c5d1e10";
const TOKEN = "Qk3pYz0aLxVwq8RtNm2cB7sDfGh4JkLp";
const USDC =
  "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1";

const ESCROW = "escrow.arsune.near";
const HASH = "3f1a9c0b7d2e4f6a8b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a";

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

type Api = {
  bounty: ReturnType<typeof makeBounty>;
  calls: { method: string; path: string; body: unknown }[];
  funding: Record<string, unknown>[];
  verifyError?: string;
  quoteError?: string;
};

async function mockApi(
  page: Page,
  mode: Mode,
  url = "http://127.0.0.1:8541/shop/",
): Promise<Api> {
  const state: Api = { bounty: makeBounty(mode, url), calls: [], funding: [] };
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
    if (path === `/api/bounties/${ID}`) return json(b);
    if (path === `/api/bounties/${ID}/demo-proof`)
      return json({ published: true, proof_url: b.proof_url });
    if (path === `/api/bounties/${ID}/verify`) {
      if (state.verifyError) return json({ detail: state.verifyError }, 409);
      b.status = "open";
      return json(b);
    }
    if (path === `/api/bounties/${ID}/fund-simulated`) {
      b.budget_amount += body.amount;
      // Arsune reopens a bounty that closed only because its budget ran out.
      const perBug = b.reward_amount + b.fee_amount;
      if (b.closed_reason === "budget_spent" && b.budget_amount >= perBug)
        Object.assign(b, { status: "open", closed_reason: null });
      b.ledger.push({
        id: "f1",
        kind: "fund",
        amount: body.amount,
        account: "a".repeat(64),
        memo: "arsune:fund:11111111-2222-4333-8444-555555555555",
        tx_id: "dry-abc",
        explorer_url: null,
        settlement_mode: "dry",
        status: "completed",
        created_at: "2026-11-09T10:05:00Z",
      });
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
  return state;
}

test("the header links to the owner page in task order", async ({ page }) => {
  await mockApi(page, "dry");
  await page.goto("/bounties/new");
  const nav = page.getByRole("navigation", { name: "Tasks" });
  await expect(nav.getByRole("link")).toHaveText([
    "Website checks",
    "Incident investigation",
    "Start a bounty",
    "Swarm",
    "Local demo",
  ]);
  await expect(nav.locator("[aria-current='page']")).toHaveText(
    "Start a bounty",
  );
});

test("create validates the form and opens the bounty page", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  await page.goto("/bounties/new");
  await expect(page.getByTestId("sim-label").first()).toBeVisible();
  await page.getByRole("button", { name: "Create bounty" }).click();
  await expect(page.getByText("Enter the full address")).toBeVisible();
  await expect(page.getByText("Enter a NEAR account ID")).toBeVisible();
  expect(api.calls.some((c) => c.path === "/api/bounties")).toBe(false);

  await page.getByLabel("Budget").fill("0.5");
  await expect(page.getByTestId("preview-bugs")).toHaveText("0 proven bugs");
  await expect(
    page.getByText("The budget must cover at least one reward and its fee."),
  ).toBeVisible();
  await page.getByLabel("Budget").fill("12.5");
  await expect(page.getByTestId("preview-fee")).toHaveText("0.10 USDC");
  await expect(page.getByTestId("preview-bugs")).toHaveText("11 proven bugs");

  await page.getByRole("button", { name: "Use the local demo shop" }).click();
  await expect(page.getByLabel("Site URL")).toHaveValue(
    "http://127.0.0.1:8541/shop/",
  );
  await expect(page.getByLabel("Owner account")).toHaveValue("demo-owner.near");
  await page.getByLabel("Base bond per claim").fill("0.25");
  await page.getByRole("button", { name: "Create bounty" }).click();
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
  await expect(page.getByTestId("step-2-status")).toHaveText(
    "Waiting for proof",
  );
});

test("create shows the API error", async ({ page }) => {
  await mockApi(page, "dry");
  await page.route("**/api/bounties", (route) =>
    route.fulfill({
      status: 422,
      json: { detail: "Private network addresses are not permitted." },
    }),
  );
  await page.goto("/bounties/new");
  await page.getByLabel("Site URL").fill("http://10.0.0.5/");
  await page.getByLabel("Owner account").fill("me.near");
  await page.getByRole("button", { name: "Create bounty" }).click();
  await expect(page.getByTestId("create-error")).toHaveText(
    "Private network addresses are not permitted.",
  );
  await expect(page).toHaveURL(/\/bounties\/new$/);
});

test("dry flow: demo proof, simulated funding, then the live board", async ({
  page,
}) => {
  const api = await mockApi(page, "dry");
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("step-1-status")).toHaveText("Done");
  await expect(page.getByTestId("step-3-status")).toHaveText("After the proof");
  await expect(page.getByTestId("proof-token")).toHaveText(TOKEN);
  await expect(page.getByTestId("proof-url")).toHaveText(
    "http://127.0.0.1:8541/.well-known/arsune-bounty.txt",
  );

  await page
    .getByRole("button", { name: "Demo site: set proof automatically" })
    .click();
  await expect(page.getByTestId("proof-done")).toContainText(
    "Ownership proven",
  );
  await expect(page.getByTestId("step-2-status")).toHaveText("Done");
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  const order = api.calls.filter((c) => c.method === "POST").map((c) => c.path);
  expect(order).toEqual([
    `/api/bounties/${ID}/demo-proof`,
    `/api/bounties/${ID}/verify`,
  ]);

  await expect(page.getByTestId("step-3-status")).toHaveText("Not funded");
  await expect(page.getByTestId("crosschain-unavailable")).toContainText(
    "dry mode",
  );
  await expect(page.getByLabel("Amount to add")).toHaveValue("10.00");
  await page.getByLabel("Amount to add").fill("abc");
  await expect(
    page.getByRole("button", { name: "Fund with simulated money" }),
  ).toBeDisabled();
  await page.getByLabel("Amount to add").fill("10");
  await page.getByRole("button", { name: "Fund with simulated money" }).click();
  await expect(page.getByTestId("fund-done")).toContainText(
    "Added 10.00 USDC of simulated money",
  );
  await expect(page.getByTestId("funded-total")).toContainText(
    "Funded 10.00 of 10.00 USDC",
  );
  await expect(page.getByTestId("funded-total")).toContainText(
    "pays for 9 proven bugs",
  );
  await expect(page.getByTestId("fund-rows")).toContainText("Simulated");
  await expect(page.getByTestId("step-3-status")).toHaveText("Funded");
  const fund = api.calls.find((c) => c.path.endsWith("/fund-simulated"));
  expect(fund?.body).toEqual({ amount: 10_000_000 });

  await expect(page.getByTestId("step-4-status")).toHaveText("Ready");
  await expect(page.getByTestId("board-link")).toHaveAttribute(
    "href",
    `/swarm?bounty=${ID}`,
  );
});

test("a top-up reopens a bounty whose budget ran out", async ({ page }) => {
  const api = await mockApi(page, "dry");
  Object.assign(api.bounty, {
    status: "closed",
    closed_reason: "budget_spent",
    budget_amount: 400_000,
  });
  await page.goto(`/bounties/${ID}`);
  await expect(page.getByTestId("bounty-status")).toHaveText("Closed");
  await expect(page.getByTestId("reopen-hint")).toHaveText(
    "The budget ran out, so the bounty is closed. Add at least 0.70 USDC to open it again.",
  );
  await page.getByLabel("Amount to add").fill("0.7");
  await page.getByRole("button", { name: "Fund with simulated money" }).click();
  await expect(page.getByTestId("bounty-status")).toHaveText("Open");
  await expect(page.getByTestId("reopen-hint")).toHaveCount(0);
  await expect(page.getByTestId("funded-total")).toContainText(
    "pays for 1 proven bug",
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
  await expect(page.getByTestId("step-2-status")).toHaveText(
    "Waiting for proof",
  );

  api.verifyError = undefined;
  await page.getByRole("button", { name: "Check ownership" }).click();
  await expect(page.getByTestId("proof-done")).toBeVisible();
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
    page.getByRole("button", { name: "Fund with simulated money" }),
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
  await expect(page.getByTestId("funded-total")).toContainText(
    "Funded 9.00 of 10.00 USDC",
  );
});

test("an unknown bounty says so", async ({ page }) => {
  await mockApi(page, "dry");
  await page.goto("/bounties/00000000-0000-4000-8000-000000000000");
  await expect(page.getByTestId("load-error")).toContainText("Not found");
  await expect(
    page.getByRole("link", { name: "Start a new bounty" }),
  ).toBeVisible();
});

test("a failed signed transfer gets a new fund_id and memo, a 422 keeps them", async ({
  page,
}) => {
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  api.bounty.status = "open";
  const replies = [504, 422];
  await page.route(`**/api/bounties/${ID}/fund`, (route) => {
    const status = replies.shift() ?? 200;
    return route.fulfill({ status, json: { detail: "no" } });
  });
  await page.goto(`/bounties/${ID}`);
  await page.getByText("Signed transfer instructions").click();
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

test("decided claims show the evidence hash, its bundle and the verify command", async ({
  page,
  context,
  baseURL,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const api = await mockApi(page, "dry", "https://shop.example.com/");
  const decided = "8d3b5a4e-1f2c-4d6e-9a7b-0c1d2e3f4a5b";
  Object.assign(api.bounty, {
    status: "open",
    budget_amount: 8_900_000,
    claims: [
      {
        id: decided,
        hunter_account: "hunter-a.near",
        claim_type: "broken_link",
        status: "unverified",
        outcome: "unverified",
        evidence_hash: HASH,
        evidence_url: `/api/claims/${decided}/evidence`,
      },
      {
        id: "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f",
        hunter_account: "hunter-b.near",
        claim_type: "horizontal_overflow",
        status: "checking",
        outcome: null,
        evidence_hash: null,
        evidence_url: null,
      },
    ],
  });
  await page.goto(`/bounties/${ID}`);
  const rows = page.getByTestId("decided-claim");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("unverified");
  await expect(rows.first()).toContainText(`claim ${decided}`);
  const hash = rows.first().getByTestId("evidence-hash");
  await expect(hash).toHaveText(`sha256 ${HASH.slice(0, 12)}…`);
  await expect(hash).toHaveAttribute("title", HASH);
  await expect(rows.first().getByTestId("evidence-link")).toHaveAttribute(
    "href",
    `/api/claims/${decided}/evidence`,
  );
  await rows
    .first()
    .getByRole("button", { name: "Copy evidence hash" })
    .click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(HASH);
  await expect(page.getByTestId("verify-hint")).toContainText(
    `uv run python -m app.verify ${new URL(baseURL!).origin} <claim_id>`,
  );
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
    page.getByRole("button", { name: "Fund with simulated money" }),
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
