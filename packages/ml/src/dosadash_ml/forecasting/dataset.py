"""Daily-sales loaders.

- `synthetic_daily_sales`: straight from datagen (train/CI — same generator
  the DB seeder uses, so a model trained here scores seeded prod sanely)
- prod loading lives in the worker task (async SQLAlchemy, apps/api) to keep
  this package DB-free
"""

from datetime import date as date_type

import pandas as pd

from dosadash_ml.datagen import MENU_ITEMS, generate_orders, generate_users
from dosadash_ml.forecasting.features import ItemMeta


def synthetic_daily_sales(
    *, users: int = 500, days: int = 365, seed: int = 42, end: date_type | None = None
) -> tuple[pd.DataFrame, list[ItemMeta], date_type, date_type]:
    """(sparse sales df, item metas, start, end) from the synthetic world.

    Item ids are 1-based positions in MENU_ITEMS — matching the seeder's
    insertion order is *not* assumed anywhere: training only uses ids to
    group series, and scoring re-derives ids from the real DB.
    """
    synth_users = generate_users(n=users, seed=seed)
    orders = generate_orders(synth_users, days=days, seed=seed, end=end)
    end = end or max(o.placed_at.date() for o in orders)
    start = min(o.placed_at.date() for o in orders)

    id_by_name = {m.name: idx + 1 for idx, m in enumerate(MENU_ITEMS)}
    counts: dict[tuple[int, date_type], float] = {}
    for order in orders:
        day = order.placed_at.date()
        for line in order.items:
            key = (id_by_name[line.item_name], day)
            counts[key] = counts.get(key, 0.0) + line.qty

    sales = pd.DataFrame(
        [{"item_id": item_id, "date": day, "qty": qty} for (item_id, day), qty in counts.items()]
    )
    metas = [
        ItemMeta(item_id=idx + 1, category=m.category, is_veg=m.is_veg)
        for idx, m in enumerate(MENU_ITEMS)
    ]
    return sales, metas, start, end
