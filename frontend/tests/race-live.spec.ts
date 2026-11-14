import { expect, test } from "@playwright/test";

// One real race from the UI: needs the local stack, its demo shop, and an idle race host.
// Run with ARSUNE_LIVE_RACE=1; it creates a bounty and runs scripted agents (dry money).
test.skip(!process.env.ARSUNE_LIVE_RACE, "Set ARSUNE_LIVE_RACE=1 to run.");

test("a real race on the demo shop, from the start page to the results", async ({
  page,
}, testInfo) => {
  test.setTimeout(300_000);
  const shot = async (name: string) => {
    const path = testInfo.outputPath(name);
    await page.screenshot({ path, fullPage: true });
    console.log("SCREENSHOT", path);
  };
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "Try the demo shop" }).click();
  await page.getByRole("button", { name: "Start a bug race" }).click();
  await expect(page).toHaveURL(/\/bounties\/[0-9a-f-]{36}$/);
  const id = new URL(page.url()).pathname.split("/").pop()!;
  console.log("BOUNTY", id);

  // The page sets the demo proof on load.
  await expect(page.getByTestId("step-1-status")).toHaveText("Done", {
    timeout: 30_000,
  });
  await expect(page.getByTestId("step-2-status")).toHaveText("Done", {
    timeout: 120_000,
  });
  await page.getByRole("button", { name: "Add simulated money" }).click();
  await expect(page.getByTestId("funded-total")).toHaveText(
    "Funded 50.00 USDC · pays for 45 bugs · 50.00 USDC left",
  );

  const step = page.getByTestId("step-4");
  await expect(page.getByTestId("step-4-status")).toHaveText("Ready to start", {
    timeout: 180_000,
  });
  const host = await (await page.request.get("/swarm-api/host")).json();
  await expect(step.getByRole("radio")).toHaveCount(0);
  await page.getByRole("button", { name: "Start the race" }).click();
  await expect(page.getByTestId("step-4-status")).toHaveText("Running");
  await expect(page.getByTestId("live-race")).toBeVisible();
  await expect(page.getByTestId("line-agents")).toHaveText(
    String(
      host.agents.hunters +
        host.agents.scouts +
        (host.ai_active ? host.agents.ai_hunters + host.agents.ai_scouts : 0),
    ),
    { timeout: 60_000 },
  );
  await expect
    .poll(
      async () => Number(await page.getByTestId("line-claims").innerText()),
      {
        timeout: 120_000,
      },
    )
    .toBeGreaterThan(0);
  await shot("live-race-running-1440.png");

  await page.goto(`/swarm?bounty=${id}`);
  await expect(page.getByTestId("other-race-banner")).toHaveCount(0);
  await expect(page.getByTestId("claim-row").first()).toBeVisible();
  await shot("live-race-board-1440.png");

  await page.goto(`/bounties/${id}`);
  const finished = page.getByTestId("step-4-status");
  // The race ends when it is stopped, or by itself when it spends the pool: then the
  // bounty closes and step 4 waits for more money.
  const ended = /^(Finished|After funding)$/;
  await expect(finished).toHaveText(/^(Running|Finished|After funding)$/);
  if ((await finished.innerText()) === "Running") {
    await page.getByRole("button", { name: "Stop the race" }).click();
  }
  await expect(finished).toHaveText(ended, { timeout: 60_000 });
  if ((await finished.innerText()) === "After funding")
    await expect(page.getByTestId("race-locked")).toHaveText(
      /^The budget is spent\./,
    );
  await expect(page.getByTestId("results")).toBeVisible();
  const results = await page.getByTestId("results").innerText();
  expect(results).not.toMatch(/[0-9a-f]{64}/);
  await shot("live-race-finished-1440.png");
});
