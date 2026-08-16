# 06 — Data Architecture & Schema

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
