// Sign in with NEAR: with a saved session, the header shows the account, "My hunts" lists the
// account's hunts from the API, and a new hunt is locked to the account and sends the session.
import { expect, Page, test } from "@playwright/test";

const ACCOUNT = "owner.near";
const SESSION = {
  account_id: ACCOUNT,
  session: "test-session",
  expires_at: Math.floor(Date.now() / 1000) + 3600,
};

function bounty(id: string, url: string, created: string) {
  return {
    id,
    url,
    origin: new URL(url).origin,
    owner_account: ACCOUNT,
    status: "open",
    closed_reason: null,
    paused_reason: null,
    reward_amount: 100_000,
    bond_amount: 10_000,
    fee_bps: 1000,
    fee_amount: 10_000,
    budget_amount: 500_000,
    budget_target: 1_000_000,
    settlement_mode: "dry",
    created_at: created,
  };
}

const MINE = bounty(
  "11111111-1111-4111-8111-111111111111",
  "https://mine.example/",
  "2026-11-14T10:00:00Z",
);
const OTHER = bounty(
  "22222222-2222-4222-8222-222222222222",
  "https://other.example/",
  "2026-11-14T09:00:00Z",
);

async function mock(page: Page, signedIn: boolean) {
  const calls: { path: string; auth: string | null; body: unknown }[] = [];
  if (signedIn)
    await page.addInitScript((s) => {
      localStorage.setItem("arsune-owner-session", JSON.stringify(s));
    }, SESSION);
  await page.route("**/api/**", (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    calls.push({
      path,
      auth: req.headers()["authorization"] ?? null,
      body: req.postDataJSON?.() ?? null,
    });
    if (path === "/api/owner/bounties")
      return req.headers()["authorization"] === "Bearer test-session"
        ? route.fulfill({
            json: {
              account_id: ACCOUNT,
              items: [MINE],
              settlement_mode: "dry",
            },
          })
        : route.fulfill({ status: 401, json: { detail: "Sign in" } });
    if (path === "/api/bounties" && req.method() === "GET")
      return route.fulfill({
        json: { items: [MINE, OTHER], settlement_mode: "dry" },
      });
    if (path === "/api/bounties" && req.method() === "POST")
      return route.fulfill({
        status: 201,
        json: { ...MINE, id: "33333333-3333-4333-8333-333333333333" },
      });
    if (path === "/api/funding/options")
      return route.fulfill({
        json: {
          settlement_mode: "dry",
          demo_site_url: "http://127.0.0.1:8541",
          crosschain: { available: false, reason: "dry", tokens: [] },
        },
      });
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });
  await page.route("**/swarm-api/**", (route) =>
    route.fulfill({ status: 500, body: "down" }),
  );
  return calls;
}

test("signed out: the header offers sign in, and My hunts has no own list", async ({
  page,
}) => {
  await mock(page, false);
  await page.goto("/races");
  await expect(page.getByTestId("owner-sign-in")).toHaveText(
    "Sign in with NEAR",
  );
  await expect(page.getByTestId("race-row")).toHaveCount(2);
  await expect(page.getByTestId("my-races")).toHaveCount(0);
});

test("signed in: My hunts lists the account's hunts from the API", async ({
  page,
}) => {
  const calls = await mock(page, true);
  await page.goto("/races");
  await expect(page.getByTestId("owner-signed-in")).toContainText(ACCOUNT);
  const mine = page.getByTestId("my-race-row");
  await expect(mine).toHaveCount(1);
  await expect(mine).toContainText("mine.example");
  await expect(page.getByTestId("race-row")).toHaveCount(2);
  expect(calls.find((c) => c.path === "/api/owner/bounties")?.auth).toBe(
    "Bearer test-session",
  );

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByTestId("owner-sign-in")).toBeVisible();
  await expect(page.getByTestId("my-races")).toHaveCount(0);
});

test("signed in: a new hunt belongs to the account and sends the session", async ({
  page,
}) => {
  const calls = await mock(page, true);
  await page.goto("/");
  const owner = page.locator("input[name=owner]");
  await expect(owner).toHaveValue(ACCOUNT);
  await expect(owner).toHaveAttribute("readonly", "");
  await page.locator("input[name=url]").fill("https://mine.example/");
  await page.getByRole("button", { name: /Test my site/ }).click();
  await expect(page).toHaveURL(/\/bounties\/33333333-/);
  const create = calls.find(
    (c) => c.path === "/api/bounties" && c.body !== null,
  );
  expect(create?.auth).toBe("Bearer test-session");
  expect((create?.body as { owner_account: string }).owner_account).toBe(
    ACCOUNT,
  );
});

test("the header fits a phone screen when signed in", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mock(page, true);
  await page.goto("/");
  await expect(page.getByTestId("owner-signed-in")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});
