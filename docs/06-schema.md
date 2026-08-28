# 06 — Data Architecture & Schema

> Original planning doc. The core shape below shipped as designed; the
> **As-Built Additions** section at the bottom lists tables/keyspaces/events
> added by later phases. Ground truth = `apps/api` models + Alembic migrations.

## Postgres (single instance: OLTP + vectors + FTS)

```
users            (id, phone UNIQUE, name, tg_user_id, role, loyalty_points, created_at)
otp_requests     (phone, otp_hash, channel DEMO|TELEGRAM, attempts, expires_at)
refresh_tokens   (user_id, token_hash, rotated_at, revoked)
addresses        (user_id, label, line1, pincode, is_default)
user_preferences (user_id, diet VEG|VEGAN|JAIN|NONVEG, allergens[], spice_level, language)

brands           (id, name)                    -- multi-brand ready day 1
menu_items       (id, brand_id, name, description, price, category, is_veg,
                  spice_level, prep_minutes, gst_rate, is_available,
                  schedule JSONB, image_url, embedding vector(1536))
customizations   (item_id, name, price_delta)
combos           (id, item_ids[], price, source MANUAL|AI_SUGGESTED,
                  status DRAFT|APPROVED|REJECTED)
ingredients      (id, name, unit, stock_qty, reorder_point, supplier, cost)
recipe_ingredients (item_id, ingredient_id, qty)   -- single source of truth:
                                                   -- drives inventory depletion AND RAG allergen KB

orders           (id, user_id, brand_id, channel_id, status, subtotal, gst, total,
                  coupon_id, eta_predicted, address_id, placed_at, ...)
order_items      (order_id, item_id, qty, customizations JSONB, unit_price)
payments         (order_id, provider, provider_order_id, status, signature_verified)
channels         (WEB | TELEGRAM | MOCK_AGGREGATOR)

reviews          (order_id, user_id, rating, text, sentiment, aspects JSONB,
                  owner_reply, reply_source AI_DRAFT|MANUAL)
coupons          (code, type PCT|FLAT, value, segment, valid_from, valid_to, usage_limit)
coupon_redemptions (coupon_id, user_id, order_id)

forecasts        (dish_id, date, predicted_qty, model_version)
purchase_orders  (id, status DRAFT|APPROVED|ORDERED|RECEIVED, lines JSONB,
                  drafted_by_agent bool, approved_by, approved_via UI|TELEGRAM)
wastage_log      (ingredient_id, qty, reason, date)
customer_segments (user_id, rfm_tier, churn_risk, ltv, computed_at)   -- nightly

chat_sessions    (id, user_id, channel, created_at)
chat_messages    (session_id, role, content, trace_id)
langgraph_checkpoints (...)                    -- agent state persistence

rag_chunks       (id, content, metadata JSONB, embedding vector(1536), tsv tsvector)
                 -- HNSW index on embedding, GIN on tsv (hybrid retrieval)

settings         (business_hours JSONB, delivery_pincodes[], kitchen_paused bool)
staff_actions    (user_id, action, entity, detail JSONB, at)   -- audit log
eval_runs        (suite, score, model, git_sha, ran_at)        -- CI writes
```

## Redis Keyspaces

| Prefix | Purpose |
|---|---|
| `cache:*` | menu, hot query cache |
| `semcache:*` | semantic cache (embedding + response, cosine ≥ 0.95) |
| `otp:*` | OTP state (hash, attempts, cooldown) |
| `celery*` | task queue |
| `pubsub:orders`, `pubsub:menu`, `pubsub:settings` | event cascade |
| `ratelimit:*` | per-user + per-IP limits |

## Event Cascade Contracts

| Event | Consumers |
|---|---|
| `menu.updated` | Celery: re-embed RAG chunks; bust `cache:menu`; bot cache refresh |
| `item.86d` | AI service: `check_availability` excludes immediately |
| `kitchen.paused` | agent refusal mode; web banner; bot notice |
| `order.status.*` | WS fan-out (KDS, tracking); Telegram push |
| `po.drafted` | owner Telegram approval message |

## Synthetic Data Generator (`packages/ml/datagen`)

- 12 months of orders; weekly seasonality (weekend biryani spikes)
- Festival multipliers: Pongal ×3 (idli/pongal/vada), Diwali (sweets), Onam
- Weather noise factor; promo-day lifts
- ~500 users with taste personas (veg-only, spice-lover, filter-coffee-daily, ...)
- Reviews with planted aspect patterns (for fine-tune training labels)
- Deterministic seed → reproducible; documented as a portfolio talking point

## As-Built Additions (Phases 5–15)

### New / extended tables

```
suppliers               (Phase 6 — backfilled from free-text ingredients.supplier)
purchase_orders + purchase_order_items   (agent provenance, state machine
                        DRAFT→PENDING_APPROVAL→APPROVED→RECEIVED +REJECTED/CANCELLED)
wastage_log             (stock_after snapshots, clamp-at-0 audited)
invoices                (VLM extraction + arithmetic-verifier confidence, review queue)
escalations             (support agent — refunds are NEVER agent-executed)
user_memories           (EPISODE per placed order → "my usual")
eval_runs               (+ per-case reports; CI live-gate ingest, admin scoreboard)
menu_item_translations  (PK item_id+lang, DRAFT/APPROVED/REJECTED, provenance;
                        prices/allergens NEVER stored — canonical row stays SoT)
menu_image_drafts       (+ menu_items.image_ai — permanent ✨ AI label)
aggregator_orders       (UNIQUE aggregator+external_order_id → idempotent webhooks)
reviews                 (as planned + scoring provenance: deterministic:rating /
                        local:<int8-champion> / live model / batch:<model>)
review_batch_jobs       (durable OpenAI Batch API job state + chunk mapping)
coupons                 (+is_active, min_subtotal, max_discount, per_user_limit,
                        source MANUAL|AI_SUGGESTED) + orders.discount
forecasts, customer_segments, orders.delivered_at   (Phase 5, as planned)
menu_items.schedule     (multi-window serving schedule {day:[{start,end}…]} —
                        Phase 11 "Dosa is not available at lunch" enforcement)
feedback_reports        (Phase 13 — tiers ANON/CUSTOMER/STAFF/SYSTEM, dedupe
                        hash, PII-redacted, triage JSONB provenance, fix_pr_number)
feedback_events         (append-only lifecycle timeline)
feedback_notifications  (Telegram anchor-card state per admin)
fixer_runs              (workflow run outcomes + cost/cached-token telemetry)
```

### Redis keyspace additions

| Prefix | Purpose |
|---|---|
| `semcache:rag:*` | semantic cache (cosine ≥ 0.95, cascade-flushed on menu events) |
| `cachestats:semcache`, `cachestats:prompt` | cache observability counters (outside the flush prefix) |
| `sentinel:5xx:<minute>` | 5xx-burst counters for the sentinel |
| `ratelimit:*` | fixed-60s-window tiers (chat 20/min · auth 10/min · write 60/min · read 240/min · feedback 5/min) |
| dedicated second Redis (`redis-celery`) | Celery broker — `noeviction` (cache redis is allkeys-lru, unsafe for a broker) |

### Event cascade additions

| Event/Channel | Consumers |
|---|---|
| `menu.translation`, `menu.image` | translation/photo overlay refresh |
| `pubsub:inventory` | stock changes — deliberately OFF `pubsub:menu` (never re-embeds RAG) |
| `pubsub:feedback` | /fixer portal WS + Telegram lifecycle anchors (own channel) |
| GitHub webhook (HMAC) + 15-min reconciler | feedback lifecycle truth-sync — a missed delivery is never permanent drift |
