"""Celery worker + beat (Phase 5, docs/02 §2.4).

Runs as its own compose service (same image as the api, built with
`--extra worker`); the api process never imports this package. Nightly
schedule (IST — restaurant local time):

- 02:00 per-dish demand forecast → `forecasts` (Phase 5)
- 02:30 inventory agent draft POs (Phase 6)
- 03:00 CRM RFM + churn scoring → `customer_segments` (Phase 5)
"""
