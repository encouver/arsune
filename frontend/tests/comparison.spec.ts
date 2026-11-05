import {
  expect,
  test,
  type Page,
  type APIRequestContext,
} from "@playwright/test";
import { readFile } from "node:fs/promises";

async function completed(page: Page) {
  await expect(
    page.getByRole("button", { name: "Check website", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  const id = new URL(page.url()).searchParams.get("job");
  expect(id).toBeTruthy();
  const response = await page.request.get(`/api/jobs/${id}`);
  expect(response.ok()).toBe(true);
  const job = await response.json();
  expect(job.status).toBe("completed");
  return job;
}

async function setFixture(
  request: APIRequestContext,
  demoUrl: string,
  state: string,
) {
  const response = await request.post(
    new URL("/comparison-control", demoUrl).href,
    { data: { state } },
  );
  expect(response.ok()).toBe(true);
}

test("real demo comparison retains both reports and downloads a repair report", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Site with faults" }).click();
  const before = await completed(page);
  const savedReport = JSON.stringify(before.report);
  await page
    .getByRole("button", { name: "Compare with corrected demo" })
    .click();
  await page.waitForURL(
    (address) => address.searchParams.get("job") !== before.id,
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Before and after", exact: true }),
  ).toBeVisible();
  const after = await completed(page);
  expect(after.baseline_job_id).toBe(before.id);
  expect(after.report.comparison.summary.resolved).toBeGreaterThanOrEqual(3);
  expect(after.report.comparison.summary.remaining).toBe(0);
  await expect(
    page.getByRole("heading", { name: /^Resolved \(/ }),
  ).toContainText(String(after.report.comparison.summary.resolved));
  await expect(
    page.getByRole("link", { name: "Before report", exact: true }),
  ).toHaveAttribute("href", `/?job=${before.id}`);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Before and after", exact: true }),
  ).toBeVisible();
  const entry = page.locator("#comparison-resolved .comparison-entry").first();
  await entry
    .getByText("View before and after evidence", { exact: true })
    .click();
  await entry.getByText("Evidence and steps to repeat").first().click();
  const image = entry.getByRole("img").first();
  await expect(image).toBeVisible();
  await expect
    .poll(() =>
      image.evaluate(
        (element: HTMLImageElement) =>
          element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true);
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download repair report" }).click();
  const download = await downloadEvent;
  const html = await readFile((await download.path())!, "utf8");
  expect(html).toContain("Resolved");
  expect(html).toContain(before.id);
  expect(html).toContain(after.id);
  const retained = await (
    await page.request.get(`/api/jobs/${before.id}`)
  ).json();
  expect(JSON.stringify(retained.report)).toBe(savedReport);
  await page.getByRole("link", { name: "Before report", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Compare with corrected demo" }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("job")).toBe(before.id);
  expect(errors).toEqual([]);
});

test("real repeat keeps faults and marks an incomplete repeat as unknown", async ({
  page,
  request,
}) => {
  const config = await (await request.get("/api/config")).json();
  await setFixture(request, config.demo_urls.faulty, "faulty");
  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page
      .getByLabel("Website URL")
      .fill(new URL("/comparison/", config.demo_urls.faulty).href);
    await page
      .getByRole("button", { name: "Check website", exact: true })
      .click();
    const before = await completed(page);
    await page.getByRole("button", { name: "Check again and compare" }).click();
    await expect(
      page.getByRole("heading", { name: "Before and after", exact: true }),
    ).toBeVisible();
    const unchanged = await completed(page);
    expect(
      unchanged.report.comparison.summary.remaining,
    ).toBeGreaterThanOrEqual(3);
    expect(unchanged.report.comparison.summary.resolved).toBe(0);
    await expect(
      page.getByRole("heading", { name: /^Still present \(/ }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await setFixture(request, config.demo_urls.faulty, "failed_page");
    await page.getByRole("button", { name: "Check again and compare" }).click();
    await expect(
      page.getByRole("heading", { name: "Before and after", exact: true }),
    ).toBeVisible();
    const incomplete = await completed(page);
    expect(
      incomplete.report.comparison.summary.could_not_recheck,
    ).toBeGreaterThan(0);
    const unknownIds = incomplete.report.comparison.could_not_recheck.map(
      (entry: {
        baseline_finding_id: string;
        current_finding: { status: string } | null;
      }) => {
        expect(entry.current_finding?.status).not.toBe("rejected");
        return entry.baseline_finding_id;
      },
    );
    for (const entry of incomplete.report.comparison.resolved) {
      expect(unknownIds).not.toContain(entry.baseline_finding_id);
      expect(entry.current_finding.status).toBe("rejected");
    }
    await expect(
      page.getByRole("heading", { name: /^Could not recheck \(/ }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("heading", { name: /^Could not recheck \(/ }),
    ).toBeVisible();
    expect(
      (await (await request.get(`/api/jobs/${before.id}`)).json()).report
        .comparison,
    ).toBeUndefined();
    await setFixture(request, config.demo_urls.faulty, "new_fault");
    await page.goto(`/?job=${before.id}`);
    await page.getByRole("button", { name: "Check again and compare" }).click();
    await expect(
      page.getByRole("heading", { name: "Before and after", exact: true }),
    ).toBeVisible();
    const changed = await completed(page);
    expect(changed.report.comparison.summary.new).toBeGreaterThan(0);
    expect(changed.report.comparison.summary.resolved).toBeGreaterThan(0);
    await expect(page.getByRole("heading", { name: /^New \(/ })).toBeVisible();
    const newEntry = page.locator("#comparison-new .comparison-entry").first();
    await newEntry.getByText("View new evidence", { exact: true }).click();
    await newEntry.getByText("Evidence and steps to repeat").click();
    await expect(newEntry.getByRole("img").first()).toHaveAttribute(
      "src",
      new RegExp(`/api/jobs/${changed.id}/evidence/`),
    );
    await page.screenshot({
      path: "/tmp/arsune-comparison-mobile.png",
      fullPage: true,
    });
  } finally {
    await setFixture(request, config.demo_urls.faulty, "faulty");
  }
});

test("a saved report from the old format opens by its URL", async ({
  page,
}) => {
  await page.route("**/api/jobs/old-saved-report", (route) =>
    route.fulfill({
      json: {
        id: "old-saved-report",
        status: "completed",
        mode: "scripted",
        url: "http://example.test/",
        created_at: "2026-11-01T10:00:00Z",
        progress: { stage: "completed", message: "Done" },
        events: [],
        findings: [],
        report: {
          findings: [],
          inspected_pages: ["http://example.test/"],
          coverage: { incomplete_pages: ["http://example.test/missing"] },
        },
      },
    }),
  );
  await page.goto("/?job=old-saved-report");
  await expect(
    page.getByRole("heading", { name: "0 confirmed faults" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Check again and compare" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Before and after", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("tab", { name: "Coverage" }).click();
  await expect(page.getByRole("tabpanel")).toContainText(
    "http://example.test/missing",
  );
});
