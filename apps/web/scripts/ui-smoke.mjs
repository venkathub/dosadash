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
const ROUTES = ["/", "/orders", "/kds", "/demo", "/admin", "/fixer"];
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

// ── Tawa Noir gates (docs/17 §6) ─────────────────────────────────────────
// The home page must be complete WITHOUT the live scene, and popups must
// open inside the screen. None of this needs an API.

// 1) The static hero art is in the server HTML (it is the LCP element and
//    the fallback) — not something JavaScript adds later.
route = "/ (server HTML)";
const html = await (await fetch(BASE + "/")).text();
if (!html.includes('src="/scene/tawa.webp"')) problems.push(`${route} static hero image missing from SSR output`);

const sceneState = (p) =>
  p.evaluate(() => {
    const c = document.querySelector("canvas");
    const img = document.querySelector('img[src="/scene/tawa.webp"]');
    return {
      canvasShown: !!c && getComputedStyle(c).opacity !== "0",
      fallbackShown: !!img && getComputedStyle(img).opacity !== "0",
    };
  });
const openHome = async (label, contextOptions, init) => {
  route = label;
  const ctx = await browser.newContext(contextOptions);
  if (init) await ctx.addInitScript(init);
  const p = await ctx.newPage();
  p.on("pageerror", (err) => problems.push(`${label} pageerror: ${err.message}`));
  p.on("console", (msg) => {
    if (msg.type() === "error" && CONSOLE_FAIL.test(msg.text())) problems.push(`${label} console: ${msg.text().slice(0, 300)}`);
  });
  await p.goto(BASE + "/", { waitUntil: "load", timeout: 30000 });
  await p.waitForTimeout(3500); // past the idle-callback that would start the scene
  return { ctx, p };
};

// 2) WebGL unavailable (old GPU, blocked driver): no errors, fallback stays.
{
  const { ctx, p } = await openHome("/ (no WebGL)", {}, () => {
    const real = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (type, ...rest) {
      return /webgl/i.test(type) ? null : real.call(this, type, ...rest);
    };
  });
  const s = await sceneState(p);
  if (s.canvasShown) problems.push(`${route} canvas became visible without WebGL`);
  if (!s.fallbackShown) problems.push(`${route} static hero fallback is not visible`);
  await ctx.close();
}

// 3) Reduced motion: the scene must never start.
{
  const { ctx, p } = await openHome("/ (reduced motion)", { reducedMotion: "reduce" });
  const s = await sceneState(p);
  if (s.canvasShown) problems.push(`${route} live scene started despite prefers-reduced-motion`);
  if (!s.fallbackShown) problems.push(`${route} static hero fallback is not visible`);
  await ctx.close();
}

// 4) Ordering comes first, and popups open on screen — desktop and phone.
for (const [w, h] of [
  [1440, 900],
  [390, 844],
]) {
  const { ctx, p } = await openHome(`/ (${w}x${h})`, { viewport: { width: w, height: h } });
  const inView = (sel) =>
    p.evaluate((sel) => {
      const el = [...document.querySelectorAll(sel)].find((e) => e.getBoundingClientRect().width > 0);
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
    }, sel);
  if (!(await inView('input[type="search"]'))) problems.push(`${route} search box is not on the first screen`);
  if (w >= 1024 && !(await inView("aside[data-tray-anchor] h2"))) problems.push(`${route} cart is not on the first screen`);
  if (await p.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth))
    problems.push(`${route} page scrolls sideways`);
  // A popup opened from inside the blurred app bar was once positioned against
  // the bar and cut off. "Visible" is not enough: its box must be on screen.
  const trigger =
    w >= 640
      ? p.locator('header button[aria-label="Report a bug or request a feature"]')
      : p.locator('section[aria-labelledby="feedback-title"] button').first();
  await trigger.click();
  await p.waitForSelector('[role="dialog"]', { timeout: 5000 }).catch(() => null);
  const box = await p.evaluate(() => {
    const d = document.querySelector('[role="dialog"]');
    if (!d) return null;
    const r = d.getBoundingClientRect();
    return { ok: r.top >= 0 && r.left >= 0 && r.right <= innerWidth && r.width > 200, inBody: d.closest("header") === null };
  });
  if (!box) problems.push(`${route} feedback popup did not open`);
  else if (!box.ok || !box.inBody) problems.push(`${route} popup is clipped or trapped in the header`);
  await ctx.close();
}

await browser.close();

if (problems.length > 0) {
  console.error(`✗ UI smoke failed (${problems.length} problem(s)):`);
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`✓ UI smoke: ${ROUTES.length} routes, 0 page errors, 0 hydration errors; home page passes no-WebGL, reduced-motion, first-screen and popup checks`);
