"""12-month synthetic order history (deterministic).

Seasonality baked in (docs/06):
- weekend biryani spikes (Sat/Sun ×2.5)
- Pongal ×3 on idli/pongal/vada categories
- Diwali ×4 sweets / ×2 snacks, Onam ×2 on veg feast categories
- weather noise + gentle promo-day lifts
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from random import Random

from dosadash_ml.datagen.menu import MENU_ITEMS, SeedMenuItem, item_allergens
from dosadash_ml.datagen.users import SyntheticUser
from dosadash_shared import ChannelType, Diet

# Festival calendar (fixed dates per year for the synthetic world)
PONGAL_DAYS = {(1, 14), (1, 15), (1, 16), (1, 17)}
DIWALI_BY_YEAR = {2024: date(2024, 11, 1), 2025: date(2025, 10, 20), 2026: date(2026, 11, 8)}
ONAM_BY_YEAR = {2024: date(2024, 9, 15), 2025: date(2025, 9, 5), 2026: date(2026, 8, 26)}

PONGAL_CATEGORIES = {"Idli & Vada", "Rice & Pongal"}
ONAM_CATEGORIES = {"Rice & Pongal", "Sweets", "Dosa"}


def category_multiplier(category: str, is_veg: bool, day: date) -> float:
    """Seasonal multiplier for a (category, veg-flag) on one day.

    Single source of truth for the synthetic-world festival calendar: datagen
    uses it to *produce* demand, and the Phase 5 forecaster uses the very same
    function as a model feature — so the feature is never out of sync with the
    data-generating process.
    """
    mult = 1.0
    if day.weekday() >= 5 and category == "Biryani":
        mult *= 2.5
    if (day.month, day.day) in PONGAL_DAYS and category in PONGAL_CATEGORIES:
        mult *= 3.0
    diwali = DIWALI_BY_YEAR.get(day.year)
    if diwali and abs((day - diwali).days) <= 1:
        if category == "Sweets":
            mult *= 4.0
        elif category == "Snacks":
            mult *= 2.0
    onam = ONAM_BY_YEAR.get(day.year)
    if onam and day == onam and is_veg and category in ONAM_CATEGORIES:
        mult *= 2.0
    return mult


def demand_multiplier(item: SeedMenuItem, day: date) -> float:
    """Pure seasonal multiplier for one item on one day (unit-testable)."""
    return category_multiplier(item.category, item.is_veg, day)


def is_festival_day(day: date) -> bool:
    """True on Pongal/Diwali(±1)/Onam — kitchen + rider load runs hot."""
    if (day.month, day.day) in PONGAL_DAYS:
        return True
    diwali = DIWALI_BY_YEAR.get(day.year)
    if diwali and abs((day - diwali).days) <= 1:
        return True
    return day == ONAM_BY_YEAR.get(day.year)


_PEAK_HOURS = {12, 13, 19, 20, 21}


def _delivery_minutes(
    rng: Random, items: list[SeedMenuItem], total_qty: int, day: date, hour: int
) -> int:
    """Synthetic actual delivery duration (minutes) — the ETA-model target.

    Correlates with observable features (prep time, basket size, peak hour,
    weekend/festival load) plus noise, so an ETA regressor has real signal.
    """
    prep = max(i.prep_minutes for i in items)
    ride = rng.uniform(8.0, 18.0)
    peak = 8.0 if hour in _PEAK_HOURS else 0.0
    weekend = 5.0 if day.weekday() >= 5 else 0.0
    festival = 6.0 if is_festival_day(day) else 0.0
    noise = rng.gauss(0.0, 3.0)
    minutes = prep + 2.0 * total_qty + ride + peak + weekend + festival + noise
    return max(18, min(90, round(minutes)))


def _allowed(item: SeedMenuItem, user: SyntheticUser) -> bool:
    p = user.persona
    if p.diet in (Diet.VEG, Diet.VEGAN, Diet.JAIN) and not item.is_veg:
        return False
    if p.diet == Diet.JAIN and item.contains_onion_garlic:
        return False
    if set(p.allergens) & item_allergens(item):
        return False
    return True


@dataclass(frozen=True)
class SyntheticOrderItem:
    item_name: str
    qty: int


@dataclass(frozen=True)
class SyntheticOrder:
    user_phone: str
    placed_at: datetime
    channel: ChannelType
    items: tuple[SyntheticOrderItem, ...]
    delivered_minutes: int  # actual delivery duration — ETA regression target


def generate_orders(
    users: list[SyntheticUser],
    *,
    days: int = 365,
    end: date | None = None,
    seed: int = 42,
) -> list[SyntheticOrder]:
    """Deterministic order history for `users` over the trailing `days`."""
    rng = Random(seed)
    end = end or date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)

    # Per-user allowed items + base weights (persona category weights)
    user_items: list[list[SeedMenuItem]] = []
    user_weights: list[list[float]] = []
    for user in users:
        allowed = [i for i in MENU_ITEMS if _allowed(i, user)]
        user_items.append(allowed)
        user_weights.append([user.persona.category_weights.get(i.category, 0.2) for i in allowed])

    orders: list[SyntheticOrder] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        weather = rng.uniform(0.85, 1.15)  # rain/heat noise
        promo = 1.25 if rng.random() < 0.08 else 1.0  # occasional promo-day lift
        for u_idx, user in enumerate(users):
            p_order = (user.persona.orders_per_week / 7.0) * weather * promo
            # weekend nudge for everyone
            if day.weekday() >= 5:
                p_order *= 1.3
            if rng.random() >= min(p_order, 0.95):
                continue

            items_pool = user_items[u_idx]
            if not items_pool:
                continue
            day_weights = [
                w * demand_multiplier(i, day)
                for w, i in zip(user_weights[u_idx], items_pool, strict=True)
            ]
            n_lines = rng.choices((1, 2, 3, 4), weights=(35, 40, 18, 7), k=1)[0]
            picked: dict[str, int] = {}
            picked_items: dict[str, SeedMenuItem] = {}
            for _ in range(n_lines):
                item = rng.choices(items_pool, weights=day_weights, k=1)[0]
                picked[item.name] = picked.get(item.name, 0) + rng.choices((1, 2), (80, 20))[0]
                picked_items[item.name] = item

            hour = rng.choices(
                range(7, 23), weights=(2, 6, 8, 4, 3, 8, 10, 4, 2, 3, 5, 9, 10, 6, 3, 1)
            )[0]
            placed_at = datetime(day.year, day.month, day.day, hour, rng.randrange(60))
            channel = ChannelType.WEB if rng.random() < 0.7 else ChannelType.TELEGRAM
            delivered_minutes = _delivery_minutes(
                rng, list(picked_items.values()), sum(picked.values()), day, hour
            )
            orders.append(
                SyntheticOrder(
                    user_phone=user.phone,
                    placed_at=placed_at,
                    channel=channel,
                    items=tuple(SyntheticOrderItem(n, q) for n, q in picked.items()),
                    delivered_minutes=delivered_minutes,
                )
            )
    return orders
