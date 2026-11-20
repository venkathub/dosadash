# 17 — UI Redesign: "Tawa Noir" (cinematic live 3D)

Status: **DEPLOYED 2026-11-20** — direction approved by the owner 2026-11-18 from a working
prototype; all slices built (§5). Production results, including what the first deploy got
wrong, are in §7.
Target: **Phase 17** (`phase/17-ui-3d`). Scope: `apps/web`, visual layer. The prototype and the
rejected mockups were design scratch and are not in the repository.

History: (1) the first proposal for this phase ("Madras Pop 3D", extruded CSS depth on the existing
identity) was mocked up and **rejected** by the owner as flat, static, off-brief and too close
to the current layout. Those mockups were never committed. (2) The first Tawa Noir build put a full-viewport scene
behind four full-screen "chapters"; the owner rated its UX **3/10**: the menu was buried,
browsing was one dish at a time, text over a moving scene was hard to read, and the cart
showed no contents. The page was restructured **ordering-app first** (§3). This document
describes that version.

---

## 1. What the owner asked for, and what answers it

| Objection to the first attempt | Tawa Noir's answer |
|---|---|
| Not as per the project context | The AI order bar is the hero of the page; a "kitchen that checks its own work" chapter states the guardrail, serving windows, ETA model and photo check as customer benefits |
| Still looked flat | A live WebGL stage: photoreal dishes standing on a kitchen counter with reflections, depth, parallax |
| Not enough motion | Opening fly-in, a turning table, steam, embers, camera moves driven by scroll, dishes arcing into the tray |
| Colours / brand | New palette (soot, ember, ghee, leaf, cream). Madras Pop is retired on customer surfaces |
| Layout too similar | An ordering app: search bar, hero banner, category tabs, menu grid, persistent cart |
| UX 3/10 on the chaptered build | Menu and cart on the first screen; the scene is a banner, not a backdrop (§3) |
| Images not realistic | Dish cutouts are generated photos, not drawn shapes |

## 2. Design system

### 2.1 Colour (literal hex in `tailwind.config.js` — the Phase 10 opacity-modifier lesson)

| Token | Hex | Use |
|---|---|---|
| `soot-950 / 900 / 800 / 700` | `#0B0907 / #14100C / #1F1913 / #2E261D` | page, panels, raised panels, hairlines |
| `ember` / `ember-hot` | `#FF6A1A / #FF8F4D` | primary action, live state, glow |
| `ghee` | `#F5C451` | eyebrows, active markers, focus ring |
| `basil` | `#37C27E` | veg mark, "serving now", success |
| `chili` (existing) | `#D64545` → on dark use `#FF5D5D` | non-veg mark, errors |
| `cream / cream-dim / cream-faint` | `#F6EFE2 / #C9BDA9 / #9C907D` | text: primary / secondary / tertiary |

Text on ember is always near-black `#1A0B02`. `cream-faint` is the lowest-contrast text allowed
and is not used below 12px (the Madras Pop page failed contrast with 10px grey text).

### 2.2 Type

Unchanged: self-hosted Space Grotesk (display), Inter (body), Noto Sans Tamil. Display type is
set large, weight 600, tight tracking, sentence case. No uppercase poster blocks.

### 2.3 Surfaces

Glass panels: `soot-900` at ~58% opacity, 16px backdrop blur, 1px cream hairline, 20px radius,
one soft deep shadow. No ink borders, no hard offset shadows.

### 2.4 The scene

A WebGL canvas inside the **hero banner only** (180px tall on phones, 290px on desktop).

- **Assets** (`public/scene/*.webp`, ~1.3 MB total): five dish cutouts, one tawa cutout, one
  kitchen backdrop. Generated once with OpenAI image models from a one-off script (not committed)
  (cutouts `gpt-image-1.5`, backdrop `gpt-image-2`). They are camera-facing photo cards, not
  3D models.
- **Staging**: tawa at the centre with an ember glow; dishes on a slowly turning table around
  it; each card has a counter reflection and contact shadow; steam and embers are particles.
- **Motion**: opening fly-in once per session; hovering a menu card that has a cutout turns
  the table to that dish; "Add" makes it hop and arc into the cart.
- **Cost control**: the render loop stops when the banner scrolls out of view or the tab is
  hidden, which is most of an ordering session.
- **Dependency**: `three` pinned exactly, loaded only through a dynamic import so it stays out
  of the first-load bundle.

### 2.5 Rules the scene must obey

1. **The interface never waits for the scene.** Everything is server-rendered and usable
   before WebGL starts. The prototype's letterbox opening that hid the UI is not shipped.
2. **Static fallback is the default state.** The banner's backdrop is a CSS image and the
   tawa is an `<img>` sized by the banner (the LCP element, no layout shift). The canvas fades
   in over them when ready.
3. **Capability gate** (`lib/scene.ts → shouldRender3D`): no canvas for reduced motion,
   data-saver, or weak devices; WebGL failure falls back silently. Evaluated in an effect after
   mount, never during render (issue #120).
4. **Nothing depends on the scene.** It is `aria-hidden` decoration; no text sits on top of
   moving imagery (the desktop headline sits on a solid gradient scrim, the phone headline
   sits below the banner).
5. **AI photos stay labelled** wherever a generated image carries meaning (existing ✨ AI rule).
6. **Reduced motion**: no opening, no drift, no spin, no fly-to-tray; scene renders static.
7. **KDS never gets WebGL** (cheap-tablet rule from docs/13 stands).

## 3. Page structure — customer `/` (ordering-app first)

Design rule: **the first screen must let you find a dish and see your cart.** Decoration
never comes between the visitor and the menu.

| Region | Content | Behaviour |
|---|---|---|
| App bar (sticky) | Wordmark, search, EN/தமிழ், Orders, Demo, Login | Search filters the menu as you type; on phones it is a full-width second row |
| Hero banner | Greeting, "Say it. We'll make it.", the order bar, the live scene | Order bar is labelled **Ask Genie** and says it opens a chat; it sends the text to the existing `/chat/stream` flow |
| Category tabs (sticky) | One tab per category, diet filter, high-protein filter, result count | Tabs jump to the section and track the scroll position |
| Menu | Recommendations, then every dish grouped by category, 2 columns on desktop | Photo, veg mark, facts, serving state, price, Add → stepper. Off-window dishes stay visible but cannot be added |
| Cart, desktop | Sticky right-hand panel, always visible | Editable lines, suggestions, coupon, subtotal, off-window guard, checkout |
| Cart, phones | Bottom bar "View tray · N items · ₹X" → bottom sheet with the same panel | Sheet closes on backdrop tap, Close, or Esc |
| Trust strip | Four customer-facing AI guarantees + AI-imagery disclosure | Static copy at the foot of the menu |

Layout changes beyond a restyle (all inside the visual layer): the floating chat launcher is
gone (chat opens from the order bar or the empty cart); the cart now lists its lines; search
moved into a persistent app bar.

Not carried over from the prototype: full-screen chapters, the one-at-a-time turntable panel,
hard-coded prices, a canned assistant reply, a fake order number, and an accuracy statistic.

## 4. Invariants

- No API, WebSocket, SSE, localStorage-key, role or route change. Agent menu context is
  byte-identical; the live-eval gate must not trigger.
- Cart, coupon, checkout, 401/400 handling and the off-window guard keep their exact logic.
- Fonts self-hosted; no build-time network fetch; static assets smoked via the public path
  after deploy (hotfixes #94 / #102).

## 5. Slices (feat PRs → `phase/17-ui-3d`)

| Slice | Contents | State |
|---|---|---|
| **S1** tokens + scene | Tawa Noir colours, `three`, `public/scene/` assets, `lib/scene.ts` gate (+ unit test), scene module, `Scene3D` component | built |
| **S2** customer `/` | Ordering-app page on real data (app bar, hero banner, category tabs, menu grid, cart panel + mobile sheet); chat opens from the order bar | built |
| **S3** every other screen | `/orders`, `/demo`, KDS, admin (all tabs), `/fixer`, login popup, order tracker, chat, feedback, review and support boxes moved to Tawa Noir by a one-off class migration; `ui.tsx` primitives rewritten; recommendations and cart suggestions rewritten natively; Madras Pop tokens, component CSS and the transitional skin **deleted** | built |
| **S4** data-backed review | Every screen walked on a local stack with the seeded demo accounts: sign-in, order, pay, tracker advancing live as the KDS moved the order, `/orders`, all 19 admin tabs, `/fixer` — 0 page/console errors; three leftovers fixed (price format, help button, chat-over-FAB) | done |
| **S5** dish photos | **Re-scoped.** Only 8 of 60 live dishes have a photo. Shipping ~50 static AI cutouts would bypass the product rule that AI photos are owner-approved in the admin Images tab. So: (a) the admin generator is being repaired in its own PR to `main` (venkathub/dosadash#189) — `gpt-image-1` no longer exists upstream; the fix sets `gpt-image-1.5`, proven by a real generation through the production code path. It was split out of this phase because the bundled PR triggered the live eval gate, which failed at 94.86% on the untouched order agent; (b) dishes without a photo get a neutral letter tile so cards stay one shape; (c) generating + approving the remaining photos is an **owner task** in admin → AI Studio → Images. The turntable keeps its five cutouts. | done (owner task remains) |
| **S6** gates + polish | `lib/bundle.test.mts` (home first-load ≤ 128 kB gzip; three.js never in first-load, always in an async chunk); `scripts/ui-smoke.mjs` now also checks the home page with WebGL unavailable, with reduced motion, that search + cart are on the first screen, and that a popup opens inside the viewport; Lighthouse accessibility fixes; site icon; README | done |

How S3 was done, so reviewers know what to distrust: a script mapped each Madras Pop utility
to a Tawa Noir one (`bg-indigo-900 → bg-soot-900`, `text-ink → text-cream`, `bg-magenta-500 →
bg-ember`, hard shadows removed, …) across 30 files. Its one context rule: text on a bright
accent background became near-black, all other text became cream. It was verified by build,
by a grep that no legacy token name remains, by ui-smoke on all six routes, and by eye on the
shells of each screen **without data**. Tab contents, populated tables, KDS tickets and the
order tracker have not been seen populated.

Historical variant names survive in `ui.tsx` (`Btn variant="magenta" | "turmeric" | "indigo"`,
`PosterBlock`, `Zari`) so ~250 call sites did not have to change; each is documented where it
is defined. Renaming them is optional cleanup.


## 6. Acceptance

| # | Criterion | Result | How it is checked |
|---|---|---|---|
| A0 | Search and the cart are on the first screen at 1440×900; search on the first screen at 390×844 | **met** | ui-smoke |
| A1 | `/` first-load JS within budget; `three` only in an async chunk | **met** — 123.2 kB gzip sum (budget 128); Next reports 126 kB vs 120 kB before; three.js is ~135 kB gzip across two async chunks | `lib/bundle.test.mts` in CI |
| A2 | LCP and CLS on `/` no worse than baseline | **missed on the first deploy** (3.63 s / 0.26 vs 3.27 s / 0.086); fixed to 2.95 s / 0.01 on a local build, to be confirmed on prod — §7 | throttled mobile trace |
| A3 | Lighthouse accessibility 94 → 100 on `/` | **met on production** (accessibility, best practices and SEO all 100) | Lighthouse |
| A4 | Reduced motion ⇒ the live scene never starts | **met** | ui-smoke |
| A5 | WebGL unavailable ⇒ page fully usable, no errors, static hero shown | **met** | ui-smoke |
| A6 | ui-smoke green on all 6 routes | **met** | CI |
| A7 | No contract drift | **met** — this phase changes `apps/web` and docs only | diff review |
| A8 | Scored design audit improves on the 6.8 / 10 baseline | not re-scored by a second reviewer; the five baseline findings (FAB over ADD, 10px grey text, heading order, three-row mobile header, no hero focal object) are each resolved | — |

## 7. Measurements (customer `/`, production)

Lab numbers: 390×844, 4× CPU throttle, Slow 4G, cold cache, single run each. They show
direction, not field performance.

| Metric | Before (2026-11-18) | After deploy (2026-11-20) | After the load-speed fix |
|---|---|---|---|
| First-load JS for `/` (Next's column) | 120 kB | 126 kB | 126 kB |
| LCP | 3.27 s | **3.63 s — worse** | 2.95 s (local build, same throttling) |
| CLS | 0.086 | **0.26 — worse** | 0.01 (local build, same throttling) |
| Lighthouse accessibility / best practices / SEO | 94 / 96 / 100 | 100 / 100 / 100 | unchanged |
| Contrast failures | 72 nodes | 0 | 0 |
| Public smoke | — | 36 / 36 after hotfix #191 | — |

**The first deploy made loading worse**, and criterion A2 was missed. The trace named two
causes, neither of them the 3D scene:

- *LCP*: the largest element was the kitchen backdrop, set as a CSS background. The browser
  found it only after the stylesheet loaded, at low priority, ~1.5 s in (load delay 911 ms).
  Fix: the backdrop is a high-priority `<img>` in the server HTML (load delay 65 ms), and the
  static hero uses a 640px image (102 kB instead of 281 kB).
- *CLS*: 0.25 of the 0.26 came from the menu arriving. The category tabs and every dish
  section appeared at once and pushed the content below them down the screen. Fix: the tab
  row has a fixed minimum height, the menu area shows placeholder cards while loading, and
  the recommendation strip reserves its height until it resolves.

The last column is a local build against live menu data; it is to be re-measured on
production after that fix deploys.

Baseline design audit (customer `/`, 2026-11-18): 6.8 / 10 by the `/design-review` rubric,
one reviewer's judgment. Its findings — floating chat button covering ADD on mobile, 10px grey
text failing contrast, heading order, three-row mobile header, a hero with no focal object —
are each resolved. It has not been re-scored by a second reviewer.

### Production record (2026-11-20)

| Step | Result |
|---|---|
| Phase PR #190 → `main` `f66f04e` | deploy success, 10-min canary success |
| Public smoke, first run | 31 / 32 — **home page scrolled sideways** (1259px wide on a 390px phone) |
| Hotfix #191 `bf6e96f` | a screen-reader label in the recommendation tiles was absolutely positioned with no anchor, escaped the scrolling strip and widened the page; tile made `relative`. ui-smoke now mocks a sample menu + recommendations so layout is checked with content (negative control: fails at 1440 / 390 / 320 without the fix) |
| Public smoke, after hotfix | 36 / 36: six routes, seven scene images, fonts, icon, script chunks, menu API, live 3D scene drawing, search, Tamil menu, add to tray, login and feedback popups on screen, **one anonymous Dosa Genie turn from the hero order bar drafting Filter Coffee**, no console errors, no failed requests, no sideways scroll at 1440 / 390 / 320 |
| Photo-generator fix #189 `950cbfa` | own live eval gate passed (the 94.86% on #188 was wobble); deploy + canary success. Not verified in the admin UI — needs an owner sign-in |

## 8. Risks

| Risk | Mitigation |
|---|---|
| Scene costs load time or jank on mid-range phones | Async chunk after first paint; capability gate; pixel-ratio cap; loop pauses when the tab is hidden; S7 puts numbers in CI |
| Photo cards read as cardboard from odd angles | Camera only dollies and trucks; no overhead shots; cards turn at most edge-on |
| Only five dishes have cutouts | Turntable shows only dishes that have one; everything else lives in the list; S6 scales it |
| Transitional skin leaks or misses a class | Scoped to `.tawa`, finite class list, removed in S3 |
| Text over moving imagery hurts legibility | The scene is confined to the banner; menu and cart sit on solid surfaces |
| Generated food imagery misleads customers | Cutouts are scene dressing for dishes that exist on the live menu; the AI label rule stays |

## 9. Limitations

- Real-device behaviour (iOS Safari, low-end Android) is not verifiable from the build machine.
- The image set was generated once; regenerating will not reproduce identical pictures.
- Screens behind a login were checked as empty shells only (see §5, S3).
- Local `npm run ui-smoke` needs `npx playwright install chromium`, which is not installed on
  the build machine (CI installs its own).
