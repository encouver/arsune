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
    "General page text is not checked for consistency.",
  );
  await expect(page.getByRole("tabpanel")).toContainText(
    "Redirects are blocked",
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
