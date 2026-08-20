# 12 — UI/UX Redesign: "Heritage Luxe" Design System

Status: **APPROVED** (direction chosen 2026-08-21) · Phase 10 (`phase/10-ui-premium`)
Scope: `apps/web` only — visual layer. Zero API/contract changes, zero behavior changes.

---

## 1. Design direction

**Heritage Luxe** — a premium South Indian identity no aggregator app has:

- **Banana-leaf deep green** as the brand ground (the leaf a dosa is served on).
- **Brass/gold** accents (the filter-coffee dabara, temple lamps) for CTAs and highlights.
- **Warm cream** surfaces (appam batter, fresh idli) for content cards.
- **Kolam dots** (`·∙·`) as section dividers / decorative rhythm — subtle, never kitsch.
- Serif display type (**Fraunces**) for headings + wordmark; **Inter** for UI text;
  **Noto Sans Tamil** so தமிழ் renders as beautifully as English.

One brand family across all three surfaces:

| Surface | Theme | Rationale |
|---|---|---|
| Customer `/`, `/orders`, `/demo` | **Leaf-light**: cream page, green header/footer, gold CTAs | Appetizing, daylight ordering |
| KDS `/kds` | **Leaf-dark**: near-black green, high-contrast type, big touch targets | Kitchen glare, distance reading |
| Admin `/admin` | **Leaf-dark** (calmer): dark green panels, gold accents, data-dense | Backoffice, long sessions |

## 2. Design tokens

Declared as CSS variables in `globals.css`, mapped into `tailwind.config.js` (`theme.extend`).
**All new UI must use token classes — no stock `amber-*`/`stone-*` in redesigned files.**

### 2.1 Color

| Token | Hex | Use |
|---|---|---|
| `leaf-950` | `#0B1F1A` | KDS/admin page bg, darkest ground |
| `leaf-900` | `#10291F` | dark panels, customer footer |
| `leaf-800` | `#14342B` | **brand primary**, customer header, dark cards |
| `leaf-700` | `#1C4436` | hover states, elevated dark surfaces |
| `leaf-600` | `#2A5A47` | borders on dark, secondary buttons |
| `leaf-500` | `#3E7258` | subtle accents, icons on dark |
| `leaf-200` | `#BFD6C8` | secondary text on dark |
| `leaf-100` | `#DCE9DF` | primary muted text on dark |
| `brass-600` | `#A88434` | gold pressed/hover-dark |
| `brass-500` | `#C8A24B` | **gold accent**: CTAs, active tab, focus rings |
| `brass-400` | `#DDBC6E` | gold hover, highlights on dark |
| `brass-300` | `#EBD49A` | gold subtle, chips on dark |
| `cream-50` | `#FDFBF5` | lightest surface, inputs on light |
| `cream-100` | `#FBF6EC` | **customer page bg** |
| `cream-200` | `#F3EAD7` | card hover, chips on light |
| `cream-300` | `#E7D9BE` | borders on light |
| `ink-900` | `#1F2421` | primary text on light |
| `ink-600` | `#55605A` | secondary text on light |
| `ink-400` | `#8A948D` | placeholder/disabled on light |
| `chili-600/500/200` | `#B3372B / #D0483A / #F3C4BE` | danger, non-veg dot, spice |
| `veg-600/500/200` | `#256C43 / #2F8A56 / #C4E3D1` | success, veg dot, PASS |
| `turmeric-500/200` | `#D99A2B / #F4DFB4` | warning, CHECK, DRAFT |
| `sky-500/200` | `#3E7CB1 / #C3DAEC` | info, Telegram |

Semantic aliases (CSS vars): `--color-success=veg-500`, `--color-danger=chili-500`,
`--color-warning=turmeric-500`, `--color-info=sky-500`.

**Razorpay overlay theme** must track brand: `theme.color = "#14342B"` (leaf-800).

### 2.2 Typography

Self-hosted variable fonts in `public/fonts/` (committed woff2, ~310 KB total; **no
build-time Google fetch** — deploys stay deterministic, postmortem-#71 spirit).

| Family | Role | Notes |
|---|---|---|
| Fraunces (400–700 var, opsz) | `font-display` — wordmark, page/section headings, big ₹ totals | soft serif, `opsz` auto |
| Inter (400–700 var) | `font-sans` — everything else | `font-feature-settings: "tnum"` on tables/prices |
| Noto Sans Tamil (400–700 var) | Tamil fallback in both stacks | தமிழ் matches Latin weight |

Scale: 12 (`meta`) · 13 (`table`) · 14 (`body-sm`) · 15 (`body`) · 17 (`lead`) ·
20/24/30/38 (`h3/h2/h1/display`, Fraunces). Headings `tracking-tight`; label rows
(`.eyebrow`) = 11px uppercase `tracking-[0.14em]` brass/ink-400.

### 2.3 Shape, elevation, texture

- Radius: `rounded-lg` (8) inputs/small buttons · `rounded-xl` (12) cards · `rounded-2xl` (16) modals/hero · `rounded-full` chips.
- Shadows (warm-tinted, never gray): `shadow-card` `0 1px 2px rgb(31 36 33 / .06), 0 4px 16px rgb(31 36 33 / .06)` · `shadow-lift` (hover) · `shadow-modal` `0 24px 64px rgb(11 31 26 / .35)`.
- Gold CTA gradient: `linear-gradient(180deg, #DDBC6E, #C8A24B)` + 1px `brass-600` border + `text-leaf-900` — utility `.btn-gold`.
- Kolam divider: `.kolam` — a row of `·∙•∙·` dots in brass-400/60, used under section headings.
- Dark panels get a 1px inner top highlight (`border-t border-white/5`) for depth.

### 2.4 Motion

- `transition-colors duration-150` on all interactive elements; `hover:-translate-y-0.5 hover:shadow-lift duration-200` on cards/CTAs.
- `animate-fade-up` (200ms, 8px rise) for modals, chat bubbles, KDS card entry.
- `animate-pulse-soft` for live dots (KDS `● live`, order tracker active step).
- Respect `prefers-reduced-motion` (component-layer media query kills transforms).

### 2.5 Focus & a11y

- Focus: `focus-visible:ring-2 ring-brass-500 ring-offset-2` (offset color = surface).
- Contrast: all text tokens ≥ 4.5:1 on their surface (brass-500 on leaf-800 = large/bold text and borders only; body text on dark uses leaf-100/200).
- Emojis stay (they're the icon system and they work) but always accompanied by a text label on actionable elements.
- Touch targets ≥ 40px on KDS (kitchen = wet fingers).

## 3. Component primitives — `app/components/ui.tsx`

Single shared module replacing the 7 drifted `btnCls/inputCls/ghostBtnCls` copies.
Exports **class-string constants + tiny components** (no new deps, no CVA):

| Export | Kind | Notes |
|---|---|---|
| `Btn` (`variant: gold\|leaf\|ghost\|danger\|subtle`, `size: sm\|md\|lg`) | component | gold = primary CTA; leaf = secondary solid; ghost = outline; danger = chili |
| `Input`, `Select`, `Textarea` (`tone: light\|dark`) | components | cream-50/leaf-900 fills, brass focus ring |
| `Card` (`tone: light\|dark`, `hover?`) | component | rounded-xl + shadow-card |
| `Badge` (`tone: success\|danger\|warning\|info\|brass\|neutral`) | component | one place for ALL status colors |
| `statusBadgeTone(status)` | fn | maps order/PO/translation/QC/sentiment states → Badge tone (replaces `STATUS_COLORS`/`QC_BADGE`/`SENTIMENT_BADGE` maps) |
| `Modal` | component | overlay `bg-leaf-950/60 backdrop-blur-sm`, panel `rounded-2xl shadow-modal animate-fade-up` |
| `SectionHeading` | component | Fraunces heading + kolam divider |
| `Chip` | component | rounded-full suggestion/filter chips |
| `Th/Td` class constants | strings | 13px, `tnum`, uppercase eyebrow thead |
| `ErrorBar`, `EmptyState`, `Spinner` | components | shared feedback |

## 4. Per-page designs

### 4.1 Customer — `/` (menu · cart · checkout)

```
┌──────────────────────────────────────────────────────────────────┐
│ ███ leaf-800 header, sticky, gold hairline bottom ███            │
│  🥞 DosaDash        [ 🔎 search… cream pill ]   EN|த  ☘Veg      │
│  (Fraunces, brass)                        Orders · Login (gold)  │
├──────────────────────────────────────────────────────────────────┤
│  cream-100 page                                                  │
│  HERO STRIP (leaf-800 → leaf-700 gradient, rounded-2xl):         │
│   "Good morning ☀ — dosas are on the tawa"  ·∙•∙·  (meal hint)  │
│   [breakfast][lunch][snacks][dinner] pills — active = gold       │
│                                                                  │
│  ✨ You might like — horizontal scroll of mini cream cards       │
│                                                                  │
│  Dosas  (Fraunces h2 + kolam divider)                            │
│  ┌────────────────────────────┐ ┌────────────────────────────┐   │
│  │ [photo 96px r-lg, ✨AI]    │ │ …                          │   │
│  │ 🟢 Masala Dosa   ₹120      │ │  2-col md+, 1-col mobile   │   │
│  │ ghee-roasted… (ink-600)    │ │  card: cream-50, shadow-   │   │
│  │ 🌶🌶 · ⚠ peanut            │ │  card, hover -y lift       │   │
│  │              [ ADD ] gold  │ │  stepper: leaf outline pill│   │
│  └────────────────────────────┘ └────────────────────────────┘   │
├──────────────────────────────────────────────────────────────────┤
│ ███ CHECKOUT DOCK — leaf-900, gold top hairline, r-t-2xl ███     │
│  🧩 Add Filter Coffee ₹40  ✨ Sweet after? — brass chips         │
│  [ coupon ── Apply ]   3 items · ₹340   [ Checkout → ] gold-lg   │
└──────────────────────────────────────────────────────────────────┘
```
Key moves: header+dock go **dark green** (frames the cream menu like a banana leaf
frames food); category sections get Fraunces + kolam; price in Fraunces `tnum`;
veg/non-veg dots become bordered squares (FSSAI-style 🟢▢/🔴▢ feel); ADD → gold;
stepper − qty + becomes a leaf-outline pill. Search/veg/lang controls become cream
pills inside the dark header.

### 4.2 Customer — modals & chat

- **LoginModal / OrderTracker**: cream-50 panel `rounded-2xl shadow-modal`, Fraunces
  title, kolam under title. Tracker steps: done = veg-500 ✓ ring, active = gold
  pulse-soft, pending = cream-300 ring. Pay button gold; Razorpay theme `#14342B`.
- **ChatWidget** ("Dosa Genie"): launcher = gold round FAB with 🥞, `shadow-lift`.
  Panel header leaf-800 w/ brass title + `● online` pulse. User bubbles leaf-700
  white text (right), assistant cream-200 ink (left), `animate-fade-up`. Order-draft
  panel = cream card w/ gold hairline, items + Fraunces subtotal, `Place order`
  gold. Warnings = turmeric chip rows.

### 4.3 Customer — `/orders`

Same header family as `/`. Order cards = cream-50 `shadow-card`: `#id` Fraunces,
status `Badge`, Telegram chip sky tone. Track/Reorder = ghost leaf buttons.
ReviewBox stars: brass-500 filled ★ / cream-300 hollow, 28px tap targets.
SupportBox: same chat skin as Dosa Genie, 🛟 title "Order help".

### 4.4 KDS — `/kds`

```
┌──────────────────────────────────────────────────────────────────┐
│ leaf-950 page · header: 🔥 KITCHEN (Fraunces) · ● live (pulse)   │
│ ┌ PLACED 3 ──────┐┌ CONFIRMED 1 ───┐┌ COOKING 2 ─┐┌ READY 1 ──┐  │
│ │ column: leaf-900 r-xl, heading eyebrow brass + count pill    │  │
│ │ ┌────────────┐ │                                              │
│ │ │ #40632 🛵  │ │  card: leaf-800, r-lg, white/5 top edge,     │
│ │ │ ₹340 (Fra) │ │  15px items in leaf-100, fade-up on entry    │
│ │ │ 2× Masala… │ │  ─ status accent bar on left edge:           │
│ │ │ [📷 QC]    │ │    PLACED sky / COOKING turmeric / READY veg │
│ │ │ ▸ COOKING  │ │  advance btn = full-width gold, 44px         │
│ │ └────────────┘ │  QC verdict = Badge (PASS veg / MISMATCH     │
│ └────────────────┘  chili / CHECK turmeric / UNREADABLE muted)  │
└──────────────────────────────────────────────────────────────────┘
```
High contrast, big type, left accent bar readable from across the kitchen.
Login gate card matches admin's (below).

### 4.5 Admin — `/admin` shell + 17 tabs

- **Shell**: leaf-950 page. Header: 🥞 DosaDash **Backoffice** (Fraunces brass) +
  role chip + logout. Tab nav becomes a **grouped, scrollable rail** (borderless
  pills; active = gold pill w/ leaf-900 text; groups separated by kolam dots):
  `Operations` Menu·Orders·Inventory·Support·Reviews │ `Growth` Coupons·Combos·
  CRM·Reports │ `AI Studio` Copilot·Nutrition·Translations·Images·Evals·Costs │
  `System` Settings·Audit.
- **Panels**: every tab content sits in `Card tone=dark` (leaf-900, r-xl); section
  titles = eyebrow + Fraunces h3.
- **Tables** (Menu, Nutrition, Audit, Evals, Costs, Reports, CRM, Copilot): 13px
  `tnum`, thead eyebrow brass-300/70, zebra `odd:bg-white/[.02]`, row hover
  `bg-white/[.04]`, numeric right-aligned.
- **Row-cards** (Orders, Inventory POs, Coupons, Support tickets, Reviews,
  Translations): leaf-800 r-lg cards w/ `Badge` statuses; primary row action gold
  `sm`, destructive chili ghost.
- **🤖 AI provenance** everywhere (`model · prompt_version`): unified `.ai-meta`
  chip — leaf-700 pill, brass-300 text, 🤖 prefix. This is the portfolio story;
  make it look deliberate.
- **Charts** (Reports/Copilot/Reviews SVGs): bars `brass-500/80`, forecast line
  `leaf-200`, anomaly dots `chili-500`, grid `white/5`. Sparkbars brass; alert
  bars chili.
- **Evals scoreboard**: PASS = veg Badge, FAIL = chili; gate line pinned as eyebrow.
  **Costs**: big cache-hit numbers in Fraunces brass.
- Empty states: kolam dots + one-liner ("Inbox zero 🎉" keeps its charm).

### 4.6 `/demo`

Leaf-light prose page with a leaf-800 hero card (Fraunces "Try DosaDash"), cream
credential tables (mono chips → cream-200/ink, r-md), gold section kolams, and the
three surfaces as linked cards w/ hover lift.

## 5. What does NOT change

- All data flows, API calls, WS/SSE protocols, localStorage keys, role gates.
- Emoji icon language (labels added where actionable).
- The ✨ AI photo label (product requirement — stays permanently visible).
- No new npm dependencies. Tailwind stays v3.

## 6. Implementation plan (feat PRs → `phase/10-ui-premium`)

1. `feat/ui-design-tokens` — fonts, globals.css vars + component layer, tailwind
   theme, root layout, `ui.tsx` primitives, this doc.
2. `feat/ui-customer` — `/`, components (LoginModal, OrderTracker, ChatWidget,
   Recommendations, CheckoutSuggestions), `/orders` (+ ReviewBox, SupportBox),
   Razorpay theme color.
3. `feat/ui-kds` — KDS board.
4. `feat/ui-admin` — shell + 17 tabs (mechanical: swap constants → `ui.tsx`).
5. `feat/ui-demo` — `/demo` + polish pass (focus states, reduced motion).

Verification per PR: `npm run build` + Playwright screenshot sweep of every route
(login-gated surfaces via demo accounts) at 390px and 1280px widths.

## 7. Risks / notes

- `#f59e0b` hard-coded in OrderTracker (Razorpay theme) → must become `#14342B`.
- Fonts self-hosted: if a weight looks wrong, re-download subsets (URLs pinned in
  PR description); never switch to build-time `next/font/google` fetch.
- KDS is used on cheap tablets — avoid backdrop-blur on the board itself (GPU).
- Tamil toggle must be re-checked visually after Noto Sans Tamil lands.
