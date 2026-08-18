"""Per-dish demand forecasting (Phase 5, docs/03 #5).

- `features`: dense daily grid + lag/calendar features (shares the festival
  calendar with datagen — the feature can't drift from the world that
  generates the data)
- `dataset`: daily-sales loaders (synthetic for train/CI, Postgres for prod)
- `train`: XGBoost training + MLflow registry with `champion` alias
  (runs locally/CI only — never on the VPS, docs/02)
- `predict`: loads exported champion artifacts, recursive 14-day forecast
  (used by the nightly Celery scoring task)
"""
