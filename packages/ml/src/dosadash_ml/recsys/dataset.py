"""User–item interaction matrix from the synthetic order history.

Implicit feedback: confidence = summed quantity per (user, item). Time-split
evaluation mirrors forecasting/ETA: the trailing `valid_days` of orders are
held out, and the model is asked to rank what each user actually ordered in
that window.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import scipy.sparse as sp

from dosadash_ml.datagen import generate_orders, generate_users


@dataclass(frozen=True)
class InteractionDataset:
    user_phones: list[str]  # row order
    item_names: list[str]  # column order (canonical menu names)
    train: sp.csr_matrix  # users × items, raw qty counts (pre-alpha)
    holdout: list[set[int]]  # per user row: item columns ordered in valid window


def build_interactions(
    *, users: int = 500, days: int = 365, valid_days: int = 28, seed: int = 42
) -> InteractionDataset:
    """Deterministic dataset — same (users, days, seed) → identical matrix."""
    order_rows = generate_orders(generate_users(n=users, seed=seed), days=days, seed=seed)
    cutoff = (date.today() - timedelta(days=1)) - timedelta(days=valid_days)

    user_index: dict[str, int] = {}
    item_index: dict[str, int] = {}
    triples: dict[tuple[int, int], float] = {}
    holdout_map: dict[int, set[int]] = {}

    for order in order_rows:
        u = user_index.setdefault(order.user_phone, len(user_index))
        for line in order.items:
            i = item_index.setdefault(line.item_name, len(item_index))
            if order.placed_at.date() > cutoff:
                holdout_map.setdefault(u, set()).add(i)
            else:
                triples[(u, i)] = triples.get((u, i), 0.0) + float(line.qty)

    matrix = sp.dok_matrix((len(user_index), len(item_index)))
    for (u, i), qty in triples.items():
        matrix[u, i] = qty

    return InteractionDataset(
        user_phones=sorted(user_index, key=user_index.get),  # type: ignore[arg-type]
        item_names=sorted(item_index, key=item_index.get),  # type: ignore[arg-type]
        train=matrix.tocsr(),
        holdout=[holdout_map.get(u, set()) for u in range(len(user_index))],
    )
