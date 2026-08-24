// UI smoke gate (Phase 13 follow-up): boot the BUILT app in headless
// Chromium and fail on runtime browser errors the compile-grade checks
// cannot see — uncaught page errors and React hydration mismatches
// (the exact class of bug in issue #120, which passed every pre-merge
// check and was only observable in a live console).
//
// Deliberately runs WITHOUT an API: a fake session is planted in
// localStorage so the logged-in render paths execute (SSR always renders
// logged-out, so any render-time session read = hydration mismatch =
// caught here). Network failures are environmental noise in this setup
// and are ignored — only page JS errors and React error signatures fail
// the gate.
//
// Usage: npm run build && (npm run start &) then `npm run ui-smoke`.
// Base URL override: UI_SMOKE_BASE (defaults to http://localhost:3000).

import { chromium } from "playwright";

const BASE = process.env.UI_SMOKE_BASE ?? "http://localhost:3000";
const ROUTES = ["/", "/orders", "/kds", "/demo", "/admin"];
// Fail signatures: uncaught exceptions always; console errors only when
// they look like React runtime/hydration problems (fetch failures against
// the absent API log as console errors too — those are environmental).
const CONSOLE_FAIL = /Minified React error|hydration|did not match|#418|#423|#425/i;

const browser = await chromium.launch();
const page = await browser.newPage();

const problems = [];
let route = "(startup)";
page.on("pageerror", (err) => problems.push(`${route} pageerror: ${err.message}`));
page.on("console", (msg) => {
  if (msg.type() === "error" && CONSOLE_FAIL.test(msg.text())) {
    problems.push(`${route} console: ${msg.text().slice(0, 300)}`);
  }
});

// Fake session BEFORE any page script runs: exercises the signed-in render
// paths (the #120 bug class) with zero backend.
await page.addInitScript(() => {
  localStorage.setItem("dosadash_token", "ui-smoke-fake-token");
  localStorage.setItem(
    "dosadash_user",
    JSON.stringify({ id: 1, name: "UI Smoke", phone: "+910000000000", role: "customer" }),
  );
});

for (route of ROUTES) {
  const resp = await page.goto(BASE + route, { waitUntil: "load", timeout: 30000 });
  if (!resp || resp.status() !== 200) {
    problems.push(`${route} HTTP ${resp ? resp.status() : "no response"}`);
    continue;
  }
  // let hydration + first client render settle (hydration errors fire here)
  await page.waitForTimeout(1500);
  const title = await page.title();
  if (!/DosaDash/i.test(title)) problems.push(`${route} unexpected title: "${title}"`);
}

await browser.close();

if (problems.length > 0) {
  console.error(`✗ UI smoke failed (${problems.length} problem(s)):`);
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`✓ UI smoke: ${ROUTES.length} routes, 0 page errors, 0 hydration errors`);
