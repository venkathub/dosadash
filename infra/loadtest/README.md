# Load Testing (Phase 9 — docs/05 week 12)

Locust scenarios simulating the real traffic mix: anonymous menu browsing
(incl. Tamil `?lang=ta`), logged-in customers (demo-OTP signup → real JWTs),
env-gated order placement and agent chat.

## Run

```bash
uv sync --group loadtest          # locust is a dependency GROUP (never in CI/service images)

uv run --group loadtest locust -f infra/loadtest/locustfile.py \
    --host http://localhost:8000 --headless -u 30 -r 3 -t 2m \
    --csv infra/loadtest/out
```

Env knobs (both default OFF — they write data / spend LLM money):

| var | effect |
|---|---|
| `LOADTEST_PLACE_ORDERS=1` | Customer users place real COD orders |
| `LOADTEST_CHAT=1` | Customer users hit the order agent (**real LLM spend**) |

## Rate-limiter interplay

The api ships with inbound rate limiting (`dosadash_api/ratelimit.py`).
Logged-in customers get per-USER buckets (realistic). Anonymous browsers all
share the runner's IP, so the read tier (240/min/IP) becomes the ceiling by
design:

- **Capacity measurement** → set `API_RATE_LIMIT_ENABLED=false` on the target.
- **Limiter demo** → leave it on; 429s are marked success but counted and
  printed on shutdown (`[ratelimit] 429 responses observed …`).

Never point an unthrottled run at production during business hours.

## Measured results

See [results.md](results.md). Latest: 100 concurrent users, 0 failures,
aggregated P50 24 ms / P95 210 ms on a single-process uvicorn (prod topology).
