# 13 — UI/UX Redesign Proposal: "Madras Pop" Design System

Status: **PROPOSED** (full mockup set generated + reviewed 2026-08-24, direction chosen over
Heritage Luxe 2.0 and Filter Coffee Noir) · Target: **Phase 12** (`phase/12-ui-madras-pop`)
Scope: `apps/web` only — visual layer. Zero API/contract/behavior changes (Phase 10 pattern).

**Source of truth: `design/madras-pop/`** — 31 approved-for-planning design images covering
every page and every state:

```
design/madras-pop/
  README.md      ← frame-by-frame index: which render covers which cases
  tokens.css     ← the complete, WORKING design system (this doc summarizes it;
                   tokens.css is normative for exact values)
  fonts/         ← self-hosted woff2: Space Grotesk (2 subsets), Inter, Noto Sans Tamil
  pages/*.html   ← 30 self-contained hi-fi mockups (no JS, no external resources)
  renders/*.png  ← the 31 design images (390px mobile / 1280 / 1440 fullPage)
  index.html     ← contact sheet (open in a browser to review everything at once)
```

Mockups are working HTML against `tokens.css` — implementation is largely a faithful
translation of these files into Tailwind + `ui.tsx`, not a reinterpretation.

---

## 1. Design direction

**Madras Pop** — vibrant Chennai street-poster energy, disciplined into a clean product grid.
A deliberate break from Heritage Luxe (docs/12): light, loud, graphic. Lorry-art playfulness,
kanchipuram color, FSSAI iconography — no aggregator app looks like this.

| Surface | Treatment |
|---|---|
| Customer `/`, `/orders`, `/demo` | Light poster: off-white ground, indigo header + ticker strip, magenta hero, white cards w/ ink borders + hard shadows, turmeric ADD |
| KDS `/kds` | Indigo-950 board, high contrast, 6px state accent bars, 44px touch targets, **no blur/gradients** (cheap tablets) |
| Admin `/admin` | Calmer dark: indigo-900 panels, turmeric table headers/data accents, magenta reserved for primary actions + alerts |

Signature construction (what makes it "Madras Pop" and not generic):
- **2px ink borders + hard offset shadows** (`4px 4px 0 #1B1B3A`) on cards/buttons — no soft gray shadows anywhere
- **Zari stripe** section divider (temple-border triangles, turmeric over a magenta rail)
- **Ticker strip** under the customer header (indigo, turmeric caps text)
- **Poster blocks** for section headings (color-blocked uppercase Space Grotesk chips)
- **FSSAI-style veg/non-veg marks** (bordered square + dot) instead of plain emoji dots
- **`.ai-meta` provenance chips** (🤖 model · prompt_version) on every AI-touched element — unchanged requirement from Phase 10; the portfolio story stays visible

## 2. Design tokens (normative source: `design/madras-pop/tokens.css`)

### 2.1 Color

| Token | Hex | Use |
|---|---|---|
| `indigo-950` | `#12122B` | KDS/admin page bg |
| `indigo-900` | `#1B1B3A` | **brand ink**: text on light, headers, dark panels, borders |
| `indigo-800` | `#232347` | dark cards, ai-meta bg |
| `indigo-700` | `#2E2E5C` | elevated dark surfaces, panel borders |
| `indigo-600` | `#3D3D73` | borders/inputs on dark |
| `indigo-300/200/100` | `#8B8BC0 / #B9B6D9 / #EDEAF6` | muted → primary text on dark |
| `magenta-700/600` | `#A81848 / #C21F58` | pressed / border on magenta |
| `magenta-500` | `#D6336C` | **primary CTA**, hero blocks, poster headers |
| `magenta-400/100` | `#E85D8A / #FBE3ED` | hover-dark / chips on light |
| `turmeric-600/500/400/100` | `#D9A404 / #F2B705 / #FFCB2E / #FCEEC5` | ADD buttons, active pills, admin table headers, accents |
| `offwhite` | `#FAF7F0` | customer page bg |
| `paper` | `#FFFFFF` | cards on light |
| `sand-200/300` | `#F1EAD8 / #E5DCC8` | chips / disabled / borders on light |
| `ink / muted / faint` | `#1B1B3A / #5A5A78 / #8E8EA8` | text on light |
| `veg` | `#1E8A5A` (+`veg-100 #D7F0E3`) | success, veg mark, PASS |
| `chili` | `#D64545` (+`chili-100 #FADEDE`) | danger, non-veg mark, spice, FAIL |
| `sky` | `#3E7CB1` (+`sky-100 #DCEAF5`) | info, Telegram, PLACED |
| `warn` | `#D9A404` (+`warn-100 #FCEEC5`) | warning, CHECK, DRAFT, off-window ⏰ |

Shadows/borders: `--shadow-pop 4px 4px 0 indigo-900` · `--shadow-pop-sm 3px 3px 0` ·
dark variant `4px 4px 0 #0C0C1F` · borders always 2px solid. Focus = magenta offset shadow
(`3px 3px 0 magenta-500`) on light, ring on dark. Radius: 8 buttons/inputs · 12 cards ·
16 hero · pill chips.

**Razorpay overlay theme → `#1B1B3A`** (currently `#14342B` from Phase 10).

### 2.2 Typography

Self-hosted woff2 (committed in `design/madras-pop/fonts/`, copy to `apps/web/public/fonts/`):

| Family | Role | Notes |
|---|---|---|
| Space Grotesk (400–700 var, latin + latin-ext ~46KB) | `font-display` — wordmark, headings, poster blocks, prices, buttons, table headers | latin-ext subset REQUIRED (₹ lives there) |
| Inter (existing, keep) | `font-sans` — body/UI | `tnum` on tables/prices |
| Noto Sans Tamil (existing, keep) | Tamil fallback in both stacks | |
| Fraunces | **retires** — delete from public/fonts | |

Display style: uppercase + slight positive tracking for poster headings (`.h-display`,
`.poster-block`, eyebrows at 11px/0.16em); buttons are Space Grotesk 700.

### 2.3 Component primitives → `app/components/ui.tsx` (evolve in place)

Map 1:1 from `tokens.css` classes; keep the existing export surface so the refactor is
mechanical (Phase 10 already unified all call sites):

| Existing export | Madras Pop treatment |
|---|---|
| `Btn` variants | gold→`turmeric` (ADD/secondary-primary), new `magenta` (primary CTA), leaf→`indigo`, ghost/danger keep; all get ink border + pop shadow, disabled = sand/no shadow |
| `Card` | light = paper + ink border + pop shadow; dark = indigo-900 panel (admin: `card-panel-dark`, borderless shadow) |
| `Badge` + `statusBadgeTone()` | keep the single mapping fn; light `badge-*` + dark `badge-dk-*` palettes per tokens.css |
| `SectionHeading` | Fraunces+kolam → Space Grotesk poster-block + **zari** divider |
| `Chip` | 2px ink border pills; `chip-active` = indigo bg / turmeric text |
| `Input/Select/Textarea` | ink border, magenta focus shadow (light) / indigo-600 border (dark) |
| `.ai-meta` | indigo-800 pill, turmeric text, 🤖 prefix (dark) + `.light` variant |
| NEW: `Ticker`, `Zari`, `PosterBlock`, `FssaiMark(veg\|nonveg)` | small additions, all in mockups |

## 3. Per-page reference

Every page/state is fully specified by its mockup — see `design/madras-pop/README.md` for
the frame→cases table. Highlights the implementation must preserve:

- **Customer menu**: ticker strip; magenta hero w/ meal-period pills; recs strip w/ ai-meta;
  86'd (SOLD OUT chip, greyed, disabled ADD) vs off-window (⏰ chip w/ window text) styled
  distinctly; ✨AI photo badge on images; turmeric stepper; dark checkout dock w/ 🧩/✨ chips
  + coupon inline. Tamil mode: poster blocks drop uppercase/tracking for Tamil script
  (`.poster-block.tamil` pattern in the mockup).
- **Chat (Dosa Genie)**: PII-redaction trust line pinned at thread top; order-draft card w/
  turmeric ORDER DRAFT header bar; serving-window warnings as warn chips; voice bubble w/
  CSS waveform; ai-meta under agent bubbles.
- **KDS**: 6px left accent bars (PLACED sky / CONFIRMED magenta / COOKING turmeric / READY
  veg); late timer flips chili; channel badges; QC verdict badges; 44px turmeric advance
  buttons; **no backdrop-blur on the board**.
- **Admin shell**: grouped pill rail (Operations · Growth · AI Studio · System), active pill
  = turmeric; header keeps role chip + redacted phone. All 17 tabs mocked individually.
  **Post-approval UX revision (2026-08-24, user-requested):** at `lg+` the grouped nav is a
  **vertical left sidebar** (sticky, 224px, group labels + icon'd items, active = turmeric
  pill) instead of the mockup's horizontal rail — 17 tabs scan better vertically; the
  horizontal rail remains as the small-screen fallback. Same tokens, same grouping.
- **Evals/Costs**: the measured-numbers story (96.55% gate arc, 48.9% cache share) rendered
  as hero stats — keep real numbers wired to the real endpoints as today.

## 4. What does NOT change (Phase 10 invariants, re-affirmed)

- All data flows, API calls, WS/SSE, localStorage keys, role gates, route structure.
- Emoji icon language (labels on actionable elements), the permanent ✨ AI photo label.
- No new npm dependencies; Tailwind stays v3; no JS-driven styling.
- Agent menu context byte-identical — **no live-eval gate trigger expected** (visual-only).

## 5. Implementation plan (feat PRs → `phase/12-ui-madras-pop`)

1. `feat/ui-pop-tokens` — Space Grotesk woff2 into `public/fonts/` (+ **verify the web
   Dockerfile copies public/** — hotfix #102 lesson), globals.css vars + component layer,
   tailwind theme (**literal hex values, not bare `var()` — the Phase 10 `bg-x/95`
   opacity-modifier lesson**), root layout font stack, `ui.tsx` retheme + new primitives
   (Ticker/Zari/PosterBlock/FssaiMark), delete Fraunces.
2. `feat/ui-pop-customer` — `/` (menu, dock, hero, recs), components (LoginModal,
   OrderTracker, ChatWidget, Recommendations, CheckoutSuggestions), `/orders` (+ ReviewBox,
   SupportBox), Razorpay theme `#1B1B3A`.
3. `feat/ui-pop-kds` — board + QC modal + gate.
4. `feat/ui-pop-admin` — shell rail + 17 tabs (mechanical `ui.tsx` swap; per-tab check
   against its mockup).
5. `feat/ui-pop-demo-polish` — `/demo`, focus states, `prefers-reduced-motion`, final
   Playwright sweep.

Verification per PR (Phase 10 + hotfix lessons baked in):
- `npm run build` + Playwright screenshot sweep of every route at **390 / 1280 / 1440**,
  compared against `design/madras-pop/renders/`.
- Tamil visual check (`?lang=ta` toggle) — Noto Sans Tamil weight vs Space Grotesk.
- **Static assets smoked via the PUBLIC path in prod post-deploy** (fonts + /media —
  hotfixes #94/#102: single-file bind-mount inode + standalone-never-bundles-public/).
- Contrast pass: turmeric-on-white is large/bold/border only; body text on turmeric uses
  ink; dark surfaces use indigo-100/200 for body text (mockups already follow this).

## 6. Risks / notes

- Turmeric `#F2B705` on white fails AA for small text — tokens.css already restricts it to
  display-weight/bordered elements; keep that discipline in review.
- Hard offset shadows must never be blurred/soft — that's the identity. Resist the urge.
- KDS: cheap-tablet GPU rule stands (no blur, no large gradients on the board).
- The ticker strip is decorative static text (no marquee animation) — keep it that way for
  reduced-motion friendliness; if animated later, gate on `prefers-reduced-motion`.
- Space Grotesk has no italic — don't use italics anywhere (mockups don't).
- Fonts: if a weight looks wrong re-download subsets (Google Fonts CSS API v22 URLs), never
  switch to build-time font fetching (deterministic deploys, postmortem-#71 spirit).
