# 04 — Business Use Cases (Customer + Owner)

## A. Customer Use Cases

### A1. Auth — OTP SignUp/SignIn (no SMS provider)
```
Phone → POST /auth/otp/request
  → 6-digit OTP: hashed in Redis, TTL 5 min, max 3 attempts, 60s resend cooldown
  → Channel A (DEMO): OTP returned in response, shown as "📱 Demo SMS" banner in UI
  → Channel B (TELEGRAM): if phone linked to Telegram → bot DMs the OTP
POST /auth/otp/verify → JWT access (15m) + rotating refresh (30d, httpOnly)
```
- `OtpChannel` interface (`DemoOtpChannel` | `TelegramOtpChannel`) → real SMS (MSG91) later = one class.
- Telegram linking: `t.me/DosaDashBot?start=<link_token>` binds tg_user_id ↔ phone. Telegram Login Widget on web as alternative.
- Roles: `customer | kitchen_staff | admin | owner`.

### A2. Account Features (logged-in)
| Feature | Implementation |
|---|---|
| Order history | `/account/orders` — paginated, filters, itemized bill + GST, **1-tap Reorder** |
| Live tracking | `/track/{id}` — WS status timeline + **AI-predicted ETA** updating with kitchen queue |
| Preferences | diet (veg/vegan/Jain), allergens, spice, default address → **auto-injected into order agent context** |
| Addresses | multiple saved, default selection, pincode-validated vs delivery zone |
| Telegram parity | `/myorders` command; live status pushed as messages |
| Loyalty | order-count → points/tier (feeds CRM) |

### A3. Ordering Journeys
1. Web browse → cart → checkout (Razorpay test) → track
2. Web chat widget → conversational order → confirm → pay link
3. Telegram text → agent → inline-keyboard confirm → pay link
4. Telegram voice note → Whisper STT → same agent flow
5. Reorder from history (1 tap)
6. Claude Desktop via MCP (demo flex)

## B. Business-Owner Use Case Matrix

| # | Use Case | Module | AI Angle | Scope |
|---|---|---|---|---|
| O1 | Live ops overview: revenue, orders, AOV, top dishes | Dashboard | anomaly flags ("sales 30% below forecast") | ✅ Core |
| O2 | Menu & pricing management | Menu ops | AI combo suggestions; off-peak discount recommendations | ✅ Core |
| O3 | Order management: view/modify/cancel/refund | Admin Orders | support-agent handles routine; owner sees escalation inbox | ✅ Core |
| O4 | Inventory & procurement: stock, wastage log, POs | Inventory | forecast-driven agent drafts POs → approve in UI or Telegram | ✅ Core |
| O5 | Reports: daily/weekly/monthly sales, dish P&L, GST CSV export | Reports | forecast-vs-actual accuracy chart | ✅ Core |
| O6 | CRM: customer list, LTV, repeat rate, churn risk | CRM | RFM + churn scoring (nightly); win-back segment | ✅ Core |
| O7 | Promotions: coupons, first-order discount, happy-hour | Promos | AI suggests segment × offer (bandit-lite) | ✅ Core |
| O8 | Feedback: reviews inbox, respond, dish-level complaint trends | Reviews | fine-tuned aspect-sentiment auto-tags ("dosa – too oily ↑") + **AI-drafted replies** owner approves | ✅ Core |
| O9 | Staff & roles (RBAC) | Settings | — | ✅ Core |
| O10 | Business hours, delivery zones (pincodes), kitchen pause switch | Settings | pause propagates to web + bot + agent instantly (event cascade) | ✅ Core |
| O11 | Delivery: manual rider assignment, delivery status | Delivery-lite | ETA model includes delivery leg | ⚠️ Simplified |
| O12 | Aggregator channels (Zomato/Swiggy) | Channel manager | — | 🔶 **Simulated**: mock-aggregator webhook injects orders → proves multi-channel routing |
| O13 | Multi-brand virtual kitchens | Multi-brand | — | 🔶 Stretch (schema has `brand_id` day 1; UI later) |
| O14 | Accounting integration (Tally) | — | — | ❌ Out — CSV export covers it |
| O15 | Supplier invoice processing: photo/PDF upload → line items → PO matching → stock update | Inventory | **Document AI/OCR** (VLM extraction, confidence gating, human review queue) | ✅ Core (validated by FoodyGent restaurant case study) |
| O16 | Analytics copilot: owner asks "top 5 dishes by margin last weekend?" in chat → answer + chart | Dashboard | **Text-to-SQL agent** (read-only DB role, SQL validation, self-correction) | ✅ Core |
| O17 | Menu photo generation for new dishes (approve before publish) | Menu ops | **Image generation**, labeled as AI-generated | ✅ Core |
| O18 | Nutrition/calorie info per dish (auto-computed from recipe mapping, owner-verified) | Menu ops | **LLM batch enrichment** | ✅ Core |
| O19 | Menu localization: Tamil/Telugu/Kannada/Hindi menus + bot replies | Menu ops | **Multilingual generation** (per-language eval sets) | ✅ Core |
| O20 | Returning-customer "my usual" recognition across sessions | CRM/Agent | **Long-term agent memory** (episodic store) | ✅ Core |

## C. Admin Menu Management (O2 detail)

```
/admin/menu
├── Item CRUD: name, images, price, category, veg/spice flags, prep-time, GST rate
├── "86" toggle: instant sold-out → hides from web + Telegram + agent tools (event cascade)
├── Scheduling: breakfast items till 11am, dinner-only items
├── Customizations editor: options + price deltas (extra podi ₹20, no onion)
├── Combo builder: manual + AI-suggested (recsys co-occurrence) → 1-click approve
├── Recipe/ingredient mapping: dish → ingredients (drives inventory depletion AND
│   RAG allergen KB — single source of truth)
└── On change: Celery re-embeds RAG chunks + busts caches (AI never drifts)
```

## D. Kitchen Staff Use Cases

- KDS live queue (WS), AI priority score + predicted prep time
- Bump order through states; auto-notify customer each transition
- Flag ingredient shortage → decrements inventory expectation

## E. Design Decisions to Defend in Interviews

1. **O12 simulated aggregator** — shows integration architecture without needing Zomato/Swiggy partnership access.
2. **O13 schema-ready, UI-deferred** — scope discipline with forward design.
3. **O14 explicit cut** — knowing what NOT to build.
4. **Recipe mapping as single source of truth** — one table drives inventory math AND the RAG knowledge base.
5. **Event cascade** — business state and AI layer can never drift.

## F. As-Built Addendum (2026-08-27)

All ✅ Core use cases above shipped to production; deviations and additions:

- **O12 shipped** as planned: HMAC-verified mock-aggregator webhook → the same
  order state machine, idempotent retries, KDS channel badges, admin simulate button.
- **O19 shipped Tamil-first**: LLM-drafted translations → owner approval →
  served on `?lang=ta` + web toggle + agent aliases, with a per-language eval
  floor (ta ≥ 0.80, currently 1.00). Other languages are a registry entry away.
- **O13 remains schema-ready/UI-deferred** and **O14 remains cut**, as planned.
- **Menu rebuilt (Phase 11)**: 60 dishes from real Chennai–Trichy highway
  kitchens (millet specials, non-veg mess meals) with **per-dish serving
  windows** enforced across menu annotation, checkout (409 with the window),
  web cards, and the order agent (deterministic serving notes).
- **New surface — feedback & self-healing (Phases 13–15)**: anyone can file a
  🐞 bug/feature report from the GUI; reports (plus a production sentinel) flow
  through LLM triage, owner Telegram approval, and a cloud coding agent that
  ships fixes through the full eval-gated CI. Owner observability via the
  `/fixer` portal (pipeline board, MTTR/spend metrics) and Telegram lifecycle
  cards. This use case wasn't in the original matrix — it emerged as O21-class
  "the platform maintains itself."
- **Reviews (O8) went further than planned**: local INT8 LoRA model scores
  ~97% of reviews at ₹0 on-VPS, the residue escalates to the LLM nightly via
  the provider Batch API; AI-drafted replies carry a compensation-promise
  guardrail with published-verbatim provenance (AI_DRAFT vs MANUAL).
