# Madras Pop — DosaDash UI Redesign (from scratch)

Status: **DESIGN PROPOSAL** — full-sweep mockup set, generated 2026-08-24.
Promote to `docs/13-ui-madras-pop-design.md` if the direction is approved for an implementation phase.

## 1. Direction

**Madras Pop** — vibrant Chennai street-poster energy, disciplined into a clean product grid.
A deliberate break from Heritage Luxe (docs/12): light, loud, and graphic instead of dark and
plush. No aggregator app looks like this.

- **Indigo ink** `#1B1B3A` — brand ground (headers, KDS/admin dark surfaces, text on light)
- **Kanchipuram magenta** `#D6336C` — primary CTAs, hero blocks, poster headers
- **Turmeric** `#F2B705` — ADD buttons, active states, accents, admin table headers
- **Off-white** `#FAF7F0` — customer page ground; white cards
- Signature construction: **2px ink borders + hard offset shadows** (4px 4px 0), pill chips,
  FSSAI-style veg/non-veg marks, a **zari stripe** (temple-border triangles) as section divider,
  a ticker strip under the header
- Type: **Space Grotesk** (display, self-hosted variable) + **Inter** (UI) + **Noto Sans Tamil**
- Surfaces: customer = light poster; KDS = indigo-950 high-contrast board (44px targets, no
  blur/gradients — cheap tablets); admin = calmer dark panels, turmeric data accents
- **`.ai-meta` provenance chips** (🤖 model · prompt_version) on every AI-touched element —
  the portfolio story, made deliberate and visible

## 2. Files

```
design/madras-pop/
  tokens.css      ← full design system (colors, type, cards, buttons, chips,
                    badges light+dark, tables, zari, ticker, ai-meta, fssai)
  fonts/          ← self-hosted woff2 (Space Grotesk, Inter, Noto Sans Tamil)
  pages/*.html    ← 30 self-contained mockups (no JS, no external resources)
  renders/*.png   ← final design images (fullPage screenshots)
  index.html      ← contact sheet
```

Regenerate renders: serve `design/madras-pop/` over HTTP, screenshot each page fullPage at
its stated width (mobile 390, desktop 1280, admin 1440).

## 3. Frame index — 31 design images, all pages, all cases

### Customer (390px mobile-first + 1280px desktop)
| Render | Covers |
|---|---|
| `customer-menu-desktop.png` (1280) | header/search/lang/veg toggle, ticker, hero + meal-period pills, ✨ recs strip (`als-v4`), dish cards: in-cart stepper, ✨AI photo badge, veg/non-veg FSSAI marks, spice/allergen, **86'd sold-out**, **⏰ off-window (lunch-only)**, checkout dock w/ 🧩 combo chips + coupon applied |
| `customer-menu-mobile.png` (390) | same cases, single-column mobile + fixed dock |
| `customer-menu-tamil-mobile.png` | **Tamil mode**: மசாலா தோசை etc., canonical EN subtitles, Latin numerals preserved, `menu_translation_v1` provenance |
| `customer-chat-mobile.png` | Dosa Genie: order-draft card w/ GST subtotal, **serving-window warning chip**, voice bubble (STT), streaming state, PII-redaction trust line, `order_agent_v5` |
| `customer-checkout-mobile.png` | steppers, address, **coupon success + coupon error**, bill w/ GST 5%, **409 serving-window banner**, 🤖 ETA (`dosadash-eta/v1`), Razorpay pay CTA |
| `customer-login-otp-mobile.png` | phone entry + OTP boxes + **Telegram OTP alternative** (2 steps) |
| `customer-tracker-mobile.png` | full state machine PLACED→…→DELIVERED, active COOKING pulse, 🤖 ETA card |
| `customer-orders-mobile.png` | active/delivered/cancelled+refunded orders, **★ review box + aspect chips**, published AI-drafted owner reply, Telegram-linked chip |
| `customer-orders-desktop.png` (1280) | orders + open 🛟 support chat: **refund → escalation** flow, "refunds never auto-executed" |
| `customer-states-mobile.png` | **edge states**: search no-results, kitchen paused, 429 rate-limit, offline, empty cart, payment failed |

### KDS + Demo (1280px)
| Render | Covers |
|---|---|
| `kds-board.png` | 4-column board, accent bars per state, channel badges 🌐/🛵/📨, elapsed timers (+late), notes, 44px advance buttons, **QC verdicts PASS/CHECK**, live/WS strip |
| `kds-qc-modal.png` | 📷 plating check: expected-dish checklist, **MISMATCH verdict (computed deterministically)**, `dish_qc_v1` |
| `kds-login.png` | staff OTP gate + RBAC note |
| `demo-page.png` | demo credentials, Razorpay TEST cards, three-surface links, what-to-try |

### Admin — 17 tabs (1440px, shared shell: grouped rail Operations · Growth · AI Studio · System)
| Render | Covers |
|---|---|
| `admin-orders.png` | filters, channel badges, refund w/ audit note, expanded state-machine timeline |
| `admin-menu.png` | table w/ Tamil aliases, serving-windows text, LIVE/86'd/OFF-WINDOW, windows editor, cascade/staleness safety strips |
| `admin-inventory.png` | agent-drafted PO (needs math + guardrail note, qty editor, approve/reject, Telegram card), wastage quick-log w/ clamp, **invoice OCR review queue** (`invoice_extract_v1`, arithmetic verified, PO match) |
| `admin-support.png` | escalations inbox + transcript, resolve-with-refund (human-only), guardrail chips |
| `admin-reviews.png` | ⚠ aspect-trend spike alert, PII-redaction chip, **three scoring provenances** (`local:v2-int8` / `batch:gpt-4o-mini` / `deterministic:rating`), AI reply draft→publish w/ AI_DRAFT vs MANUAL rule |
| `admin-coupons.png` | active/inactive/redeemed-locked cards, guardrail copy (no free food), **🤖 promo-agent drafts** w/ skip reasons |
| `admin-combos.png` | approved/AI-suggested drafts, price-clamp note, lunch-only combo |
| `admin-crm.png` | 7 RFM tiers, churn/LTV, win-back ranked list w/ "usual" orders |
| `admin-reports.png` | forecast-vs-actual SVG (WAPE 0.421 vs 0.555, anomaly dots), dish P&L w/ 35% fallback flag, gst.csv |
| `admin-copilot.png` | NL→SQL, guardrail chips (SELECT-only…), result chart, **PII refusal** (`SELECT 1 AS unsupported`) |
| `admin-nutrition.png` | LLM-enriched table, allergen chips, draft/approve, low-confidence flag |
| `admin-translations.png` | draft/approved/**stale-reset** rows, numeral guardrail, gap-fill button |
| `admin-images.png` | AI photo drafts: published/draft/generating, style contract, ✨-label rule |
| `admin-evals.png` | **merge-gate banner**, 96.55% PASS stats, per-language bars w/ ta floor, run history incl. the 85%→96.67% regression-catch story |
| `admin-costs.png` | 48.9% prompt-cache hero number, daily spend stack, per-model table, litellm fallback chain |
| `admin-settings.png` | kitchen pause, multi-window weekly hours, staff RBAC, rate-limit tiers, danger zone |
| `admin-audit.png` | append-only log incl. 🤖 agent actions audited like humans |

## 4. Implementation notes (if approved)

Same shape as Phase 10: visual-only phase branch, token layer first (literal-hex Tailwind
theme — remember the `bg-x/95` opacity-modifier lesson), `ui.tsx` primitives mapped 1:1 from
`tokens.css` classes, then per-surface PRs, Playwright sweep at 390/1280/1440 pre-merge.
Razorpay overlay theme would become `#1B1B3A`. Fonts: add Space Grotesk woff2 (2 subsets,
~46KB) alongside existing Inter/Noto Tamil; Fraunces retires. Agent menu context untouched —
no live-eval gate trigger.
