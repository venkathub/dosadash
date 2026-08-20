"""Recsys tests: dataset, training (tiny), fold-in math, committed champion.

The committed-champion gates lock the portfolio story in CI: ALS must beat
the popularity baseline on Recall@10, and tail recall must be materially
above popularity's structural 0.0.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from dosadash_ml.recsys.predict import (
    fold_in_user,
    load_recsys_champion,
    popular_items,
    recommend_from_history,
)

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"

# ------------------------------------------------------------------- dataset


def test_dataset_is_deterministic_and_split():
    pytest.importorskip("scipy")
    from dosadash_ml.recsys.dataset import build_interactions

    a = build_interactions(users=60, days=90, valid_days=14, seed=7)
    b = build_interactions(users=60, days=90, valid_days=14, seed=7)
    assert a.item_names == b.item_names
    assert (a.train != b.train).nnz == 0
    assert a.train.shape == (len(a.user_phones), len(a.item_names))
    assert len(a.holdout) == len(a.user_phones)
    assert a.train.nnz > 0 and any(a.holdout)


# ------------------------------------------------------------- tiny training


def test_train_evaluate_export_roundtrip(tmp_path):
    pytest.importorskip("implicit")
    from dosadash_ml.recsys.dataset import build_interactions
    from dosadash_ml.recsys.train import evaluate, export_champion, scale_confidences, train_als

    data = scale_confidences(build_interactions(users=80, days=90, valid_days=14, seed=7), "log1p")
    model = train_als(data, factors=16, iterations=5, seed=7)
    metrics = evaluate(model, data)
    assert 0.0 <= metrics["recall_at_10"] <= 1.0
    assert metrics["evaluated_users"] > 0

    export_champion(
        model,
        data,
        metrics,
        "0",
        alpha=5.0,
        regularization=0.5,
        count_scale="log1p",
        export_dir=tmp_path,
    )
    champion = load_recsys_champion(tmp_path)
    assert champion.count_scale == "log1p"
    assert champion.item_factors.shape == (len(data.item_names), 16)

    allowed = set(champion.item_names)
    recs = recommend_from_history(champion, {champion.popularity[0]: 3.0}, k=5, allowed=allowed)
    assert recs is not None and len(recs) == 5
    assert all(name in allowed for name, _ in recs)


# ------------------------------------------------------- fold-in math (pure)


def _toy_champion(tmp_path) -> object:
    """3 items in 2-factor space: item0 ≈ item1, item2 orthogonal."""
    champ_dir = tmp_path / "recsys" / "champion"
    champ_dir.mkdir(parents=True)
    np.save(champ_dir / "item_factors.npy", np.array([[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]]))
    (champ_dir / "meta.json").write_text(
        json.dumps(
            {
                "model_version": "toy/v0",
                "item_names": ["Dosa A", "Dosa B", "Payasam"],
                "popularity": ["Payasam", "Dosa A", "Dosa B"],
                "alpha": 5.0,
                "regularization": 0.1,
                "count_scale": "raw",
            }
        )
    )
    return load_recsys_champion(tmp_path)


def test_fold_in_prefers_similar_items(tmp_path):
    champion = _toy_champion(tmp_path)
    allowed = {"Dosa B", "Payasam"}
    recs = recommend_from_history(champion, {"Dosa A": 4.0}, k=2, allowed=allowed)
    assert recs is not None
    assert recs[0][0] == "Dosa B"  # collinear with history, not the popular item
    assert recs[0][1] > recs[1][1]


def test_fold_in_unknown_history_returns_none(tmp_path):
    champion = _toy_champion(tmp_path)
    assert fold_in_user(champion, {"Pizza": 2.0}) is None
    assert recommend_from_history(champion, {"Pizza": 2.0}, k=2, allowed={"Dosa B"}) is None


def test_popular_respects_allowed_and_exclude(tmp_path):
    champion = _toy_champion(tmp_path)
    assert popular_items(champion, k=2, allowed={"Dosa A", "Dosa B"}, exclude={"Dosa A"}) == [
        "Dosa B"
    ]


# ------------------------------------------- committed champion asset gates


def test_committed_champion_loads_and_beats_popularity():
    champion = load_recsys_champion(ARTIFACTS)
    meta = json.loads((ARTIFACTS / "recsys" / "champion" / "meta.json").read_text())
    assert champion.item_factors.dtype == np.float32
    assert meta["recall_at_10"] > meta["popularity_recall_at_10"], (
        "champion must beat the popularity baseline — retrain before committing"
    )
    assert meta["tail_recall_at_10"] > 0.2  # popularity scores 0.0 here by construction
    assert champion.count_scale in ("log1p", "raw")


def test_committed_champion_names_match_seed_menu():
    from dosadash_ml.datagen import MENU_ITEMS

    champion = load_recsys_champion(ARTIFACTS)
    menu_names = {m.name for m in MENU_ITEMS}
    unknown = set(champion.item_names) - menu_names
    assert not unknown, f"champion items not in the seed menu: {unknown}"
