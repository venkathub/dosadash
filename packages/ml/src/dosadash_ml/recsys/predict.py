"""Serve-side ALS scoring — numpy only (NO implicit/scipy import here: the
ai service ships this module, and heavy train deps never reach the VPS).

The exported champion holds ITEM factors only. User vectors are computed at
request time by folding the user's live DB order history into the item-factor
space (the classic ALS least-squares step). This deliberately avoids shipping
user factors: prod users and synthetic training users never need to share ids,
and brand-new users get personalized as soon as they have history.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class RecsysChampion:
    item_factors: np.ndarray  # (n_items, factors)
    item_names: list[str]  # row order
    item_index: dict[str, int]
    popularity: list[str]  # item names, most-ordered first (train window)
    alpha: float
    regularization: float
    count_scale: str  # "log1p" | "raw" — must mirror training exactly
    version: str


def load_recsys_champion(model_dir: str | Path) -> RecsysChampion:
    champ_dir = Path(model_dir) / "recsys" / "champion"
    meta = json.loads((champ_dir / "meta.json").read_text())
    factors = np.load(champ_dir / "item_factors.npy")
    names = list(meta["item_names"])
    if factors.shape[0] != len(names):
        raise ValueError(f"factor rows {factors.shape[0]} != item names {len(names)}")
    return RecsysChampion(
        item_factors=factors,
        item_names=names,
        item_index={name: idx for idx, name in enumerate(names)},
        popularity=list(meta["popularity"]),
        alpha=float(meta["alpha"]),
        regularization=float(meta["regularization"]),
        count_scale=str(meta.get("count_scale", "raw")),
        version=str(meta["model_version"]),
    )


def fold_in_user(champion: RecsysChampion, counts: dict[str, float]) -> np.ndarray | None:
    """History {item name → qty} → user vector via the ALS normal equations:

        x_u = (Yᵀ C_u Y + λI)⁻¹ Yᵀ C_u p_u,   C_u = 1 + α·scale(count) on seen items

    where scale mirrors training's confidence scaling (meta count_scale).
    Returns None when no history item is known to the model (fall back to
    embedding/popularity instead of pretending to personalize)."""
    seen = [(champion.item_index[n], q) for n, q in counts.items() if n in champion.item_index]
    if not seen:
        return None
    y = champion.item_factors
    n_factors = y.shape[1]
    # Standard implicit-ALS decomposition: YᵀC_uY = YᵀY + Yᵀ(C_u − I)Y over seen items.
    a = y.T @ y + champion.regularization * np.eye(n_factors)
    b = np.zeros(n_factors)
    for idx, qty in seen:
        scaled = np.log1p(qty) if champion.count_scale == "log1p" else qty
        confidence = 1.0 + champion.alpha * scaled
        row = y[idx]
        a += (confidence - 1.0) * np.outer(row, row)
        b += confidence * row
    return np.linalg.solve(a, b)


def recommend_from_history(
    champion: RecsysChampion,
    counts: dict[str, float],
    *,
    k: int,
    allowed: set[str],
    exclude: set[str] = frozenset(),
) -> list[tuple[str, float]] | None:
    """Top-k (name, score) over `allowed` items, or None when the history
    doesn't overlap the model's item space (caller falls back)."""
    user_vec = fold_in_user(champion, counts)
    if user_vec is None:
        return None
    scores = champion.item_factors @ user_vec
    ranked = sorted(
        (
            (name, float(scores[idx]))
            for name, idx in champion.item_index.items()
            if name in allowed and name not in exclude
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return ranked[:k]


def popular_items(
    champion: RecsysChampion, *, k: int, allowed: set[str], exclude: set[str] = frozenset()
) -> list[str]:
    """Most-ordered training items, filtered to what is orderable right now."""
    picks = [n for n in champion.popularity if n in allowed and n not in exclude]
    return picks[:k]
