import { expect, test } from "@playwright/test";

test("a real job shows checked faults, rejected demo claim, evidence, and corrected report", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Start a website check" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Site with faults" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Site with faults" }).click();
  await expect(
    page.getByRole("button", { name: "Check in progress" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "Evidence, before advice." }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults/ }),
  ).toBeVisible();
  const confirmed = page.locator("article.finding");
  expect(await confirmed.count()).toBeGreaterThanOrEqual(3);
  await confirmed.first().getByText("Evidence and steps to repeat").click();
  const image = confirmed.first().getByRole("img").first();
  await expect(image).toBeVisible();
  await expect
    .poll(() =>
      image.evaluate(
        (element: HTMLImageElement) =>
          element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true);
  await page.getByRole("tab", { name: /Rejected/ }).click();
  await expect(
    page.getByText("Demo-only claim", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Checker result:")).toBeVisible();
  await page.getByText("Evidence and steps to repeat").click();
  const rejectedImage = page.getByRole("img").first();
  await expect(rejectedImage).toBeVisible();
  await expect
    .poll(() =>
      rejectedImage.evaluate(
        (element: HTMLImageElement) =>
          element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true);
  await page.getByRole("tab", { name: /Unverified/ }).click();
  await expect(page.getByRole("tabpanel")).toBeVisible();
  await page.getByRole("tab", { name: "Coverage" }).click();
  await expect(
    page.getByRole("heading", { name: "What this check covered" }),
  ).toBeVisible();
  await expect(page.getByText(/Inspected pages \(5\)/)).toBeVisible();
  await expect(page.getByRole("tabpanel")).toContainText(
    "Other text and ambiguous offers are not checked.",
  );
  await expect(page.getByRole("tabpanel")).toContainText(
    "At most three redirects per chain",
  );
  await expect(page.getByRole("tabpanel")).not.toContainText("data-fact-key");
  await page.screenshot({
    path: "/tmp/arsune-desktop-report.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Corrected site" }).click();
  await expect(
    page.getByRole("button", { name: "Check in progress" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("heading", { name: "0 confirmed faults" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "No confirmed findings in this report" }),
  ).toBeVisible();
  expect(failures).toEqual([]);
});

test("coverage shows real redirects and incomplete layout checks", async ({
  page,
  request,
}) => {
  const response = await request.get("/api/config");
  expect(response.ok()).toBe(true);
  const config = await response.json();
  const target = new URL("/network/start", config.demo_urls.faulty).href;
  await page.goto("/");
  await page.getByLabel("Website URL").fill(target);
  await page
    .getByRole("button", { name: "Check website", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults/ }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Coverage" }).click();
  const coverage = page.getByRole("tabpanel");
  await expect(coverage).toContainText(`Original URL: ${target}`);
  await expect(coverage).toContainText(
    `Final URL: ${new URL("/network/final/index.html", target).href}`,
  );
  await expect(coverage).toContainText("Followed:");
  await expect(coverage).toContainText("(HTTP 302)");
  await expect(coverage).not.toContainText("Redirects are blocked");
  await expect(coverage).not.toContainText("External origins are blocked");

  await page
    .getByLabel("Website URL")
    .fill(new URL("/network/missing-style", target).href);
  const completed = page.waitForResponse(async (response) => {
    if (!/\/api\/jobs\/[^/]+$/.test(response.url()) || !response.ok())
      return false;
    return (await response.json()).status === "completed";
  });
  await page
    .getByRole("button", { name: "Check website", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults/ }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Coverage" }).click();
  await expect(coverage).toContainText("Mobile layout checks are incomplete");
  await expect(coverage).toContainText(
    "These pages have not passed the layout check.",
  );
  await expect(coverage).toContainText("missing.css");
  await expect(coverage).toContainText("Page appearance can be incomplete.");
  const result = await (await completed).json();
  expect(result.report.coverage.resource_issues.length).toBeGreaterThan(0);
  await expect(coverage).toContainText(
    result.report.coverage.resource_issues[0].reason,
  );
});

test("coverage can display a report from the old format", async ({ page }) => {
  await page.route("**/api/config", (route) =>
    route.fulfill({
      json: {
        modes: ["scripted"],
        max_pages: 5,
        demo_urls: {
          faulty: "http://example.test/",
          corrected: "http://example.test/",
        },
      },
    }),
  );
  await page.route("**/api/jobs", (route) =>
    route.fulfill({
      json: {
        id: "old-report",
        status: "completed",
        mode: "scripted",
        url: "http://example.test/",
        progress: { stage: "completed", message: "Done" },
        events: [],
        findings: [],
        report: {
          findings: [],
          inspected_pages: ["http://example.test/"],
          coverage: {
            redirect_policy: "Redirects are blocked in this saved report.",
            incomplete_pages: ["http://example.test/missing"],
          },
        },
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("button", { name: "Site with faults" }).click();
  await page.getByRole("tab", { name: "Coverage" }).click();
  await expect(page.getByRole("tabpanel")).toContainText(
    "Redirects are blocked in this saved report.",
  );
  await expect(page.getByRole("tabpanel")).toContainText(
    "http://example.test/missing",
  );
  await expect(
    page.getByRole("heading", { name: "Redirect history" }),
  ).toHaveCount(0);
});

test("invalid target shows service error and mobile layout fits", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByLabel("Website URL").fill("http://127.0.0.1:12345/");
  await page
    .getByRole("button", { name: "Check website", exact: true })
    .click();
  await expect(page.locator(".error[role=alert]")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check website", exact: true }),
  ).toBeEnabled();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "/tmp/arsune-mobile-error.png",
    fullPage: true,
  });
});

test("lost progress connection keeps last state and reports the failure", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Site with faults" }),
  ).toBeEnabled();
  await page.route("**/api/jobs/*", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Connection test interruption." }),
    }),
  );
  await page.getByRole("button", { name: "Site with faults" }).click();
  await expect(page.locator(".error[role=alert]")).toContainText(
    "Progress will update when the connection returns.",
  );
  await expect(
    page.getByRole("button", { name: "Check in progress" }),
  ).toBeDisabled();
});
