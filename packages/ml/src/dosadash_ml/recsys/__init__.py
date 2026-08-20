"""Recsys (Phase 7): implicit-feedback ALS on order history.

Train-time modules (`dataset`, `train`) need the `train` extra (implicit,
scipy, mlflow); the serving module (`predict`) is numpy-only so the ai
service image stays lean (4 GB RAM budget, Hard Rule 7).
"""
