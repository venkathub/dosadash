"""Order ETA regression (Phase 5, docs/03).

Predicts actual delivery minutes at checkout from order composition + clock
features. Scoring path is xgboost + stdlib only (runs inside the ai service,
Hard Rule 7); training mirrors the demand forecaster: MLflow registry with a
`champion` alias, artifacts exported and baked into images.
"""
