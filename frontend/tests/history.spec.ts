import { expect, test } from "@playwright/test";

test("a visible price report opens from saved history with readable evidence", async ({
  page,
  request,
}) => {
  const config = await (await request.get("/api/config")).json();
  const control = new URL("/comparison-control", config.demo_urls.faulty).href;
  expect(
    (await request.post(control, { data: { state: "faulty" } })).ok(),
  ).toBe(true);
  const target = new URL("/prices/different/first", config.demo_urls.faulty)
    .href;
  await page.goto("/");
  await page.getByLabel("Website URL").fill(target);
  await page
    .getByRole("button", { name: "Check website", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  const id = new URL(page.url()).searchParams.get("job")!;
  const finding = page
    .locator(".finding")
    .filter({ has: page.getByRole("heading", { name: "Compared prices" }) })
    .first();
  await expect(finding).toBeVisible();
  await expect(finding.locator(".price-evidence")).toContainText("USD 19");
  await expect(finding.locator(".price-evidence")).toContainText("USD 29");
  await expect(finding.locator(".price-evidence")).toContainText("Basic");
  await expect(finding.locator(".price-evidence")).toContainText(
    "per month, per seat",
  );
  await expect(finding.locator(".price-evidence")).toContainText(
    "Page location:",
  );
  await expect(finding.locator("blockquote").first()).toBeVisible();
  const priceImage = finding.locator(".price-capture").first();
  await expect(priceImage).toBeVisible();
  await expect
    .poll(() =>
      priceImage.evaluate(
        (element: HTMLImageElement) =>
          element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download report JSON" }).click();
  await downloadEvent;
  await page.goto("/");
  const history = page.getByRole("region", { name: "Recent checks" });
  const saved = history
    .locator("li")
    .filter({ has: page.locator(`a[href='/?job=${id}']`) });
  await expect(saved).toContainText("completed");
  await expect(saved.locator("time")).toBeVisible();
  await saved.getByRole("link", { name: target, exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("job")).toBe(id);
  await page.getByRole("button", { name: "Check again and compare" }).click();
  await expect(
    page.getByRole("heading", { name: "Before and after", exact: true }),
  ).toBeVisible();
  const afterId = new URL(page.url()).searchParams.get("job")!;
  await page.goto("/");
  const repeat = history
    .locator("li")
    .filter({ has: page.locator(`a[href='/?job=${afterId}']`) });
  await expect(
    repeat.getByRole("link", { name: "Baseline report" }),
  ).toHaveAttribute("href", `/?job=${id}`);
  await repeat.getByRole("link", { name: "Baseline report" }).click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("job")).toBe(id);
  await page.screenshot({
    path: "/tmp/arsune-price-history.png",
    fullPage: true,
  });
});

test("history loads bounded older pages and can refresh", async ({ page }) => {
  await page.route("**/api/jobs?*", async (route) => {
    const older = new URL(route.request().url()).searchParams.has("cursor");
    await route.fulfill({
      json: {
        items: [
          {
            id: older ? "older" : "recent",
            url: older ? "https://older.test/" : "https://recent.test/",
            status: "completed",
            mode: "scripted",
            baseline_job_id: null,
            created_at: "2026-11-05T10:00:00Z",
          },
        ],
        next_cursor: older ? null : "test-cursor",
      },
    });
  });
  await page.goto("/");
  const history = page.getByRole("region", { name: "Recent checks" });
  await expect(
    history.getByRole("link", { name: "https://recent.test/" }),
  ).toBeVisible();
  await history.getByRole("button", { name: "Older checks" }).click();
  await expect(
    history.getByRole("link", { name: "https://older.test/" }),
  ).toBeVisible();
  await expect(
    history.getByRole("button", { name: "Older checks" }),
  ).toHaveCount(0);
  await history.getByRole("button", { name: "Refresh" }).click();
  await expect(
    history.getByRole("link", { name: "https://recent.test/" }),
  ).toBeVisible();
});

test("ambiguous prices show exclusions instead of a passed price check", async ({
  page,
  request,
}) => {
  const config = await (await request.get("/api/config")).json();
  const target = new URL(
    "/prices/ambiguous-dollar/first",
    config.demo_urls.faulty,
  ).href;
  await page.goto("/");
  await page.getByLabel("Website URL").fill(target);
  await page
    .getByRole("button", { name: "Check website", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Coverage" }).click();
  const panel = page.getByRole("tabpanel");
  await expect(panel).toContainText("prices excluded");
  await expect(panel).toContainText(
    "Excluded prices were not checked. This does not mean that they passed.",
  );
  await expect(panel).toContainText("$19 per month, per seat");
  await expect(panel).toContainText("Page location:");
});
