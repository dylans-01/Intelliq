import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import path from "node:path";

const ADMIN = { email: "admin@intelliq.local", password: "e2e-admin-password" };
const VIEWER = { email: "viewer@example.com", password: "viewer-password-1" };
const FIXTURE = path.resolve(__dirname, "../../apps/api/tests/fixtures/superstore_overview.twb");

async function login(page: Page, who: { email: string; password: string }) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/dashboards");
}

async function ensureViewer(request: APIRequestContext) {
  await request.post("/api/auth/login", { data: ADMIN });
  const me = await (await request.get("/api/auth/me")).json();
  const res = await request.post("/api/admin/users", {
    data: { email: VIEWER.email, name: "E2E Viewer", password: VIEWER.password, role: "viewer" },
    headers: { "X-Intelliq-CSRF": me.csrf_token },
  });
  expect([200, 409]).toContain(res.status());
}

const kpi = (page: Page, title: string) => page.locator(`section[aria-label="${title}"] .viz-kpi__value`);

test("dashboard renders, cross-filters and resets", async ({ page }) => {
  await login(page, ADMIN);
  await page.getByText("Sales Performance (sample)").click();
  await expect(kpi(page, "Orders")).toHaveText("5,000");
  await expect(kpi(page, "Sales")).toHaveText("$13M");

  // Select the first bar (West, the largest) in "Sales by region".
  const chart = page.locator('[data-worksheet="by_region"] canvas');
  const box = (await chart.boundingBox())!;
  await page.mouse.click(box.x + box.width * 0.17, box.y + box.height * 0.6);
  await expect(kpi(page, "Orders")).toHaveText("1,420");

  // The table twin of the filtered chart lists only West rows.
  await page.locator('[data-worksheet="managers"]').getByRole("button", { name: "Worksheet options" }).click();
  await page.getByRole("menuitem", { name: "View as table" }).click();
  await expect(page.locator('[data-worksheet="managers"] tbody tr')).toHaveCount(1);

  await page.getByRole("button", { name: "Reset view" }).click();
  await expect(kpi(page, "Orders")).toHaveText("5,000");
});

test("dashboard filters narrow every worksheet", async ({ page }) => {
  await login(page, ADMIN);
  await page.getByText("Sales Performance (sample)").click();
  await page.getByRole("button", { name: "Segment" }).click();
  await page.getByRole("option", { name: "Corporate" }).click();
  await page.keyboard.press("Escape");
  await expect(kpi(page, "Orders")).not.toHaveText("5,000");
  const filtered = await kpi(page, "Orders").textContent();
  expect(Number(filtered!.replace(/,/g, ""))).toBeLessThan(5000);
});

test("viewers cannot reach authoring surfaces", async ({ page, request }) => {
  await ensureViewer(request);
  await login(page, VIEWER);
  await expect(page.getByRole("navigation", { name: undefined }).getByText("Metrics")).toHaveCount(0);
  await page.getByText("Sales Performance (sample)").click();
  await expect(kpi(page, "Sales")).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
  await page.goto("/admin");
  await expect(page.getByText("You don't have access to this")).toBeVisible();
});

test("author builds and saves a chart", async ({ page }) => {
  await login(page, ADMIN);
  await page.getByRole("button", { name: "New dashboard" }).click();
  await page.getByLabel("Dashboard title").fill("E2E dashboard");
  await page.getByRole("button", { name: "Add chart" }).click();
  await page.getByRole("button", { name: "Add Region to the selected chart" }).click();
  await page.getByRole("button", { name: "Add Sales to the selected chart" }).click();
  await expect(page.locator(".bld-item canvas")).toBeVisible();
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText(/Saved · v1|Saved version 1/).first()).toBeVisible();
  await page.goto("/dashboards");
  await expect(page.getByText("E2E dashboard")).toBeVisible();
});

test("tableau workbook import produces a report and a draft dashboard", async ({ page }) => {
  await login(page, ADMIN);
  await page.goto("/import");
  await page.locator("input[type=file]").setInputFiles(FIXTURE);
  await page.waitForURL("**/import/*");
  await expect(page.getByText("Uses unsupported function(s): REGEXP_EXTRACT")).toBeVisible();
  await page.getByRole("button", { name: /Create 1 draft dashboard/ }).click();
  await page.getByRole("button", { name: "Open dashboard" }).click();
  await expect(page.getByRole("heading", { name: "Executive Overview" })).toBeVisible();
  await expect(page.getByText("Imported from superstore_overview.twb")).toBeVisible();
  await expect(page.locator('[data-worksheet="sales_by_region"] canvas')).toBeVisible();
});
