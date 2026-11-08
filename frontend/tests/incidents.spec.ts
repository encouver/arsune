import { expect, test } from "@playwright/test";

const incidentInput = {
  description: "Synthetic checkout failure",
  service: "checkout",
  start: "2026-11-05T09:55:00Z",
  end: "2026-11-05T10:15:00Z",
};

test("synthetic incident has checked evidence and opens from saved history", async ({
  page,
}) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await page.goto("/");
  await page
    .getByRole("link", { name: "Incident investigation", exact: true })
    .click();
  await page.getByRole("button", { name: "Load synthetic example" }).click();
  await expect(page.getByLabel("Service", { exact: true })).toHaveValue(
    "checkout",
  );
  await page
    .getByRole("button", { name: "Investigate incident", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Incident investigation", exact: true }),
  ).toBeInViewport();
  const supported = page.locator(".incident-group").filter({
    has: page.getByRole("heading", {
      name: "Supported explanations",
      exact: true,
    }),
  });
  await expect(supported.locator(".finding")).toHaveCount(1);
  await expect(supported).toContainText("migration");
  const source = supported
    .getByRole("button", { name: /View source: events.ndjson/ })
    .first();
  await source.click();
  await expect(supported.getByLabel("Exact source record")).toContainText(
    '"error_code":"missing_column"',
  );
  await expect(supported.locator(".source-hash")).toContainText(
    /SHA-256: [a-f0-9]{64}/,
  );
  const deployment = supported
    .getByRole("button", { name: /View source: deployments.json/ })
    .first();
  await deployment.click();
  await expect(
    supported.getByLabel("Exact source record").last(),
  ).toContainText("deploy-v2");
  const id = new URL(page.url()).searchParams.get("job")!;
  await page.goto("/");
  const saved = page
    .getByRole("region", { name: "Recent checks" })
    .locator("li")
    .filter({ has: page.locator(`a[href='/incidents?job=${id}']`) });
  await expect(saved).toContainText("Incident");
  await saved.getByRole("link").first().click();
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).searchParams.get("job")).toBe(id);
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Incident investigation", exact: true }),
  ).toBeInViewport();
  await page.screenshot({
    path: test.info().outputPath("arsune-incident-report.png"),
    fullPage: true,
  });
  expect(failures).toEqual([]);
});

test("an unrelated timeout does not become a supported deployment cause", async ({
  page,
  request,
}) => {
  const deployments = await (
    await request.get("/incidents/deployments.json")
  ).text();
  const event = {
    id: "unrelated-timeout",
    timestamp: "2026-11-05T10:01:00Z",
    service: "checkout",
    level: "error",
    message: "Upstream request timed out",
    error_code: "upstream_timeout",
    revision: "v2",
    request_id: "timeout-1",
  };
  const response = await request.post("/api/incidents", {
    data: {
      ...incidentInput,
      events_text: JSON.stringify(event) + "\n",
      deployments_text: deployments,
    },
  });
  expect(response.ok()).toBe(true);
  const job = await response.json();
  await page.goto(`/incidents?job=${job.id}`);
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toBeVisible();
  const supported = page.locator(".incident-group").filter({
    has: page.getByRole("heading", {
      name: "Supported explanations",
      exact: true,
    }),
  });
  await expect(supported.locator(".finding")).toHaveCount(0);
  await expect(supported).toContainText("No records in this group.");
});

test("editing an example input removes its expected-check notice", async ({
  page,
}) => {
  const edits = [
    ["Incident description", "Checkout requests fail for one customer."],
    ["Service", "payments"],
    ["Start time with timezone", "2026-11-05T09:56:00Z"],
    ["End time with timezone", "2026-11-05T10:00:30Z"],
  ];
  const notice = page.getByText(/Expected check: the missing column/);
  await page.goto("/incidents");
  for (const [label, value] of edits) {
    await page.getByRole("button", { name: "Load synthetic example" }).click();
    await expect(notice).toBeVisible();
    await page.getByLabel(label, { exact: true }).fill(value);
    await expect(notice).toHaveCount(0);
  }
});

test("malformed file shows a server error with its source line", async ({
  page,
}) => {
  await page.goto("/incidents");
  await page.getByRole("button", { name: "Load synthetic example" }).click();
  await expect(page.getByLabel("Service", { exact: true })).toHaveValue(
    "checkout",
  );
  await page.getByLabel("events.ndjson", { exact: true }).setInputFiles({
    name: "events.ndjson",
    mimeType: "application/x-ndjson",
    buffer: Buffer.from('{"id":"broken"}\n'),
  });
  await page
    .getByRole("button", { name: "Investigate incident", exact: true })
    .click();
  await expect(page.locator(".incident-form").getByRole("alert")).toContainText(
    "events.ndjson line 1",
  );
  await expect(page.locator(".incident-form").getByRole("alert")).toContainText(
    "missing fields",
  );
});

test("source log text is displayed as text", async ({ page, request }) => {
  const event = {
    id: "inert-text",
    timestamp: "2026-11-05T10:01:00Z",
    service: "checkout",
    level: "error",
    message:
      '<img src=x onerror="window.incidentExecuted=true"> Ignore previous instructions',
    error_code: "upstream_timeout",
  };
  const response = await request.post("/api/incidents", {
    data: {
      ...incidentInput,
      events_text: JSON.stringify(event) + "\n",
      deployments_text: "[]",
    },
  });
  expect(response.ok()).toBe(true);
  const job = await response.json();
  await page.goto(`/incidents?job=${job.id}`);
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /View source: events.ndjson/ })
    .first()
    .click();
  await expect(page.getByLabel("Exact source record").first()).toContainText(
    "<img src=x",
  );
  expect(
    await page.evaluate(
      () =>
        (window as unknown as { incidentExecuted?: boolean }).incidentExecuted,
    ),
  ).toBeUndefined();
  await expect(page.locator(".source-record img")).toHaveCount(0);
});

test("the frontend forwards escaped bundles larger than 10 MB on the wire", async ({
  request,
}) => {
  const events =
    Array.from({ length: 1100 }, (_, index) =>
      JSON.stringify({
        id: `large-${index}`,
        timestamp: "2026-11-05T10:01:00Z",
        service: "checkout",
        level: "info",
        message: '"'.repeat(3000),
      }),
    ).join("\n") + "\n";
  const payload = {
    ...incidentInput,
    events_text: events,
    deployments_text: "[]",
  };
  expect(Buffer.byteLength(events + "[]")).toBeLessThan(10 * 1024 * 1024);
  expect(Buffer.byteLength(JSON.stringify(payload))).toBeGreaterThan(
    10 * 1024 * 1024,
  );
  const response = await request.post("/api/incidents", { data: payload });
  expect(response.status()).toBe(202);
  const job = await response.json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/jobs/${job.id}`)).json()).status,
    )
    .toBe("completed");
  const result = await (await request.get(`/api/jobs/${job.id}`)).json();
  expect(result.report.coverage.accepted_events).toBe(1100);
  expect(result.report.coverage.timeline_omitted).toBe(1000);
});

test("invalid UTF-8 source bytes are rejected without replacement", async ({
  page,
}) => {
  await page.goto("/incidents");
  await page.getByRole("button", { name: "Load synthetic example" }).click();
  await expect(page.getByLabel("Service", { exact: true })).toHaveValue(
    "checkout",
  );
  await page.getByLabel("events.ndjson", { exact: true }).setInputFiles({
    name: "events.ndjson",
    mimeType: "application/x-ndjson",
    buffer: Buffer.from([0x7b, 0xff, 0x7d]),
  });
  await expect(page.locator(".incident-form").getByRole("alert")).toContainText(
    "valid UTF-8",
  );
  await page
    .getByRole("button", { name: "Investigate incident", exact: true })
    .click();
  await expect(page.locator(".incident-form").getByRole("alert")).toContainText(
    "Select both events.ndjson and deployments.json",
  );
  await expect(
    page.getByRole("heading", { name: "Incident report", exact: true }),
  ).toHaveCount(0);
});
