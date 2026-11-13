import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

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
  await page.goto("/demo");
  await expect(
    page.getByRole("heading", { name: /Try both checks/ }),
  ).toBeVisible();
  await expect(
    page.getByText(/All demo checks use browser checks only/),
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
}, testInfo) => {
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
  const offlinePath = testInfo.outputPath("offline-incident-report.html");
  await download.saveAs(offlinePath);
  const offline = await context.newPage();
  const external: string[] = [];
  offline.on("request", (request) => {
    if (/^https?:/.test(request.url())) external.push(request.url());
  });
  await context.setOffline(true);
  try {
    await offline.goto(pathToFileURL(offlinePath).href);
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

test("every page shows the same brand and the three race links", async ({
  page,
}) => {
  const main = [
    ["/", "Start a race"],
    ["/races", "Your races"],
    ["/swarm", "Live race"],
  ];
  // The older tools keep their URLs; the header links none of them.
  const tools = ["/checks", "/incidents", "/demo"];
  // /swarm renders its header inside <main>, so it has no banner landmark to check here.
  for (const [path, current] of [
    ...main.filter(([p]) => p !== "/swarm"),
    ...tools.map((p) => [p, null]),
  ]) {
    await page.goto(path!);
    await expect(
      page.getByRole("banner").getByRole("link", { name: "Arsune home" }),
    ).toBeVisible();
    await expect(page.getByRole("banner").locator(".brand-mark")).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Tasks" });
    await expect(nav.getByRole("link")).toHaveText(main.map(([, l]) => l));
    for (const [href, label] of main)
      await expect(
        nav.getByRole("link", { name: label, exact: true }),
      ).toHaveAttribute("href", href);
    await expect(page.getByText("More tools")).toHaveCount(0);
    if (current)
      await expect(nav.locator("[aria-current='page']")).toHaveText(current);
    else await expect(nav.locator("[aria-current='page']")).toHaveCount(0);
  }
});

test("a late download response for an earlier job does not reach the next job", async ({
  page,
}) => {
  const investigate = page.getByRole("button", {
    name: "Investigate incident",
    exact: true,
  });
  const download = page.getByRole("button", {
    name: "Download incident report",
    exact: true,
  });
  await page.goto("/incidents?example=known");
  await expect(page.getByLabel("Service", { exact: true })).toHaveValue(
    "checkout",
  );
  await investigate.click();
  await expect(download).toBeVisible();
  const firstId = new URL(page.url()).searchParams.get("job")!;
  const firstReport = `**/api/incidents/${firstId}/report`;
  let release = () => {};
  const held = new Promise<void>((resolve) => (release = resolve));
  await page.route(firstReport, async (route) => {
    await held;
    await route
      .fulfill({ status: 409, json: { detail: "Late failure for job A." } })
      .catch(() => undefined);
  });
  const aborted = page.waitForEvent("requestfailed", (request) =>
    request.url().endsWith(`/api/incidents/${firstId}/report`),
  );
  await download.click();
  await expect(
    page.getByRole("button", { name: "Downloading report…" }),
  ).toBeDisabled();

  await investigate.click();
  await page.waitForURL((url) => url.searchParams.get("job") !== firstId);
  const secondId = new URL(page.url()).searchParams.get("job")!;
  await expect(page.getByText(`Job ${secondId}`)).toBeVisible();
  await expect(download).toBeEnabled();
  await aborted;
  release();
  await page.unrouteAll({ behavior: "wait" });
  await expect(page.locator(".incident-report").getByRole("alert")).toHaveCount(
    0,
  );

  const downloadEvent = page.waitForEvent("download");
  await download.click();
  expect((await downloadEvent).suggestedFilename()).toBe(
    `arsune-incident-${secondId}.html`,
  );
});
