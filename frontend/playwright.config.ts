import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 180_000,
  expect: { timeout: 120_000 },
  workers: 1,
  use: {
    baseURL: process.env.ARSUNE_UI_URL || "http://127.0.0.1:3540",
    headless: true,
    launchOptions: process.env.ARSUNE_CHROME_PATH
      ? {
          executablePath: process.env.ARSUNE_CHROME_PATH,
          args: ["--no-sandbox"],
        }
      : undefined,
    screenshot: "only-on-failure",
  },
});
