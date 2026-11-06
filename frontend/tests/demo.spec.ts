import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

const reportHeading = { name: "Incident report", exact: true };

function supported(page: import("@playwright/test").Page) {
  return page.locator(".incident-group").filter({
    has: page.getByRole("heading", {
      name: "Supported explanations",
      exact: true,
    }),
  });
}

test("local demo uses real website and incident checks with a timeout control", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("link", { name: "Local demo", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /Try both checks/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/All demo checks use scripted mode/),
  ).toBeVisible();
  await page.getByRole("link", { name: "Open website demo" }).click();
  await page.getByRole("button", { name: "Site with faults" }).click();
  await expect(
    page.getByRole("heading", { name: /\d+ confirmed faults?/ }),
  ).toBeVisible();
  const beforeId = new URL(page.url()).searchParams.get("job");
  const before = await (await page.request.get(`/api/jobs/${beforeId}`)).json();
  expect(before.mode).toBe("scripted");
  expect(before.report.provider).toEqual({
    name: "scripted",
    live_call: false,
  });
  await page
    .getByRole("button", { name: "Compare with corrected demo" })
    .click();
  await page.waitForURL((url) => url.searchParams.get("job") !== beforeId);
  await expect(
    page.getByRole("heading", { name: "Before and after", exact: true }),
  ).toBeVisible();
  const afterId = new URL(page.url()).searchParams.get("job");
  const after = await (await page.request.get(`/api/jobs/${afterId}`)).json();
  expect(after.mode).toBe("scripted");
  expect(after.report.provider).toEqual({ name: "scripted", live_call: false });
  expect(after.report.comparison.summary.resolved).toBeGreaterThanOrEqual(3);
  expect(after.report.comparison.summary.remaining).toBe(0);

  await page.goto("/demo");
  await page
    .getByRole("link", { name: "Load known incident", exact: true })
    .click();
  await expect(page.getByLabel("Service", { exact: true })).toHaveValue(
    "checkout",
  );
  await expect(
    page.getByText(/Expected check: the missing column/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Investigate incident", exact: true })
    .click();
  await expect(page.getByRole("heading", reportHeading)).toBeVisible();
  await expect(supported(page).locator(".finding")).toHaveCount(1);
  const knownId = new URL(page.url()).searchParams.get("job");
  const known = await (await page.request.get(`/api/jobs/${knownId}`)).json();
  expect(known.mode).toBe("scripted");
  expect(known.report.provider).toBeNull();
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download incident report", exact: true })
    .click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe(`arsune-incident-${knownId}.html`);
  const html = await readFile((await download.path())!, "utf8");
  expect(html).toContain("checkout_token");
  expect(html).toContain("deploy-v2");
  expect(html).toContain("events.ndjson");

  await page.goto("/demo");
  await page
    .getByRole("link", { name: "Load unrelated timeout control", exact: true })
    .click();
  await expect(page.getByLabel("Incident description")).toHaveValue(/time out/);
  await expect(page.getByText(/Expected check: the timeout/)).toBeVisible();
  await page
    .getByRole("button", { name: "Investigate incident", exact: true })
    .click();
  await expect(page.getByRole("heading", reportHeading)).toBeVisible();
  await expect(supported(page).locator(".finding")).toHaveCount(0);
  await expect(supported(page)).toContainText("No records in this group.");
  expect(errors).toEqual([]);
});

test("download keeps unsafe source text inert offline and shows server errors", async ({
  page,
  context,
}) => {
  const marker =
    '<img src="https://invalid.example/source" onerror="window.incidentExecuted=true">';
  const response = await page.request.post("/api/incidents", {
    data: {
      description: "Unsafe source text check",
      service: "checkout",
      start: "2026-11-05T09:55:00Z",
      end: "2026-11-05T10:15:00Z",
      events_text:
        JSON.stringify({
          id: "unsafe-text",
          timestamp: "2026-11-05T10:01:00Z",
          service: "checkout",
          level: "error",
          message: marker,
          error_code: "upstream_timeout",
        }) + "\n",
      deployments_text: "[]",
    },
  });
  expect(response.status()).toBe(202);
  const { id } = await response.json();
  await page.goto(`/incidents?job=${id}`);
  await expect(page.getByRole("heading", reportHeading)).toBeVisible();
  await page.route(`**/api/incidents/${id}/report`, (route) =>
    route.fulfill({
      status: 409,
      json: { detail: "Source evidence is unavailable." },
    }),
  );
  await page
    .getByRole("button", { name: "Download incident report", exact: true })
    .click();
  await expect(
    page.locator(".incident-report").getByRole("alert"),
  ).toContainText("Source evidence is unavailable.");
  await expect(page).toHaveURL(new RegExp(`/incidents\\?job=${id}`));
  await page.unroute(`**/api/incidents/${id}/report`);
  const downloadEvent = page.waitForEvent("download");
  await page
    .getByRole("button", { name: "Download incident report", exact: true })
    .click();
  const download = await downloadEvent;
  const html = await readFile((await download.path())!, "utf8");
  expect(html).toContain("&lt;img");
  expect(html).not.toContain('<img src="https://invalid.example/source"');
  const offlinePath = `/tmp/arsune-incident-offline-${id}.html`;
  await download.saveAs(offlinePath);
  const offline = await context.newPage();
  const external: string[] = [];
  offline.on("request", (request) => {
    if (/^https?:/.test(request.url())) external.push(request.url());
  });
  await context.setOffline(true);
  try {
    await offline.goto(`file://${offlinePath}`);
    await expect(offline.locator("#evidence-1 pre")).toContainText(
      JSON.stringify({
        id: "unsafe-text",
        timestamp: "2026-11-05T10:01:00Z",
        service: "checkout",
        level: "error",
        message: marker,
        error_code: "upstream_timeout",
      }),
    );
    await expect(offline.locator("img, script, iframe")).toHaveCount(0);
    expect(
      await offline.evaluate(
        () =>
          (window as unknown as { incidentExecuted?: boolean })
            .incidentExecuted,
      ),
    ).toBeUndefined();
    expect(external).toEqual([]);
  } finally {
    await context.setOffline(false);
    await offline.close();
  }
});
