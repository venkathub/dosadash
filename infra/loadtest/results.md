# Load Test Results

## 2026-08-20 — local dev box (baseline before prod off-peak run)

**Environment** (honesty caveat): local laptop (IdeaPad Flex 5, Ryzen-class
CPU), PostgreSQL 16 (pgvector) + Redis 7 in Docker, api as ONE uvicorn
process (matches prod topology — the compose api runs a single worker on the
4 GB VPS). Seeded DB: 52 menu items, 40 users, 256 orders. Numbers are
indicative of the app's efficiency, not of VPS hardware; a prod off-peak
re-run should be recorded below after the Phase 9 deploy.

### Capacity pass — limiter OFF, orders ON (real checkouts incl. mock payment)

`-u 100 -r 10 -t 90s`, `LOADTEST_PLACE_ORDERS=1`, `API_RATE_LIMIT_ENABLED=false`

| endpoint | # reqs | fails | P50 | P95 | P99 |
|---|---|---|---|---|---|
| GET /api/v1/menu | 1225 | 0 | 26 ms | 200 ms | 290 ms |
| GET /api/v1/menu/categories | 321 | 0 | 8 ms | 120 ms | 210 ms |
| GET /api/v1/menu/items/[id] | 332 | 0 | 21 ms | 240 ms | 350 ms |
| GET /api/v1/menu?lang=ta | 176 | 0 | 32 ms | 280 ms | 360 ms |
| GET /api/v1/orders (history) | 339 | 0 | 21 ms | 160 ms | 340 ms |
| **POST /api/v1/orders (checkout)** | **91** | **0** | **69 ms** | **370 ms** | **470 ms** |
| POST /api/v1/auth/otp/request | 40 | 0 | 220 ms | 460 ms | 500 ms |
| POST /api/v1/auth/otp/verify | 40 | 0 | 60 ms | 330 ms | 350 ms |
| **Aggregated** | **2564** | **0 (0.00%)** | **24 ms** | **210 ms** | **350 ms** |

- **28.6 req/s sustained, 100 concurrent users, zero failures.**
- 91 real orders placed end-to-end (item validation → state machine → mock
  payment capture → order event publish).
- 30-user realistic-pacing pass (same config): 8.0 req/s, 0 failures,
  aggregated P95 43 ms.

### Limiter demo — rate limiting ON (defaults: read 240/min, auth 10/min)

`-u 60 -r 20 -t 60s`, single source IP (worst case for the anonymous tier):

- **343 requests shed with 429** (`Retry-After` set) while served traffic
  held **P50 17 ms** — the limiter degrades abusive traffic, not the service.
- Direct probe: `GET /menu/categories` × 260 rapid → exactly **240 × 200,
  then 20 × 429** (fixed-window contract, to the request).

### Not measured here

- Agent chat under load (`LOADTEST_CHAT=1`) — deliberately skipped locally:
  it spends real LLM money and mostly measures provider latency, not our
  service. A short prod off-peak chat pass (with semcache warm) belongs in
  the prod section below.

## Prod off-peak run — TODO after Phase 9 deploy

(Record here: same passes against the VPS, limiter on, small user counts.)
