from datetime import date
from decimal import Decimal

from dosadash_ml.datagen import (
    MEAL_PERIODS,
    MENU_ITEMS,
    PERSONAS,
    category_multiplier,
    demand_multiplier,
    generate_orders,
    generate_users,
    is_festival_day,
    item_allergens,
    validate_menu,
)
from dosadash_shared import Diet

# ------------------------------------------------------------------ menu seed


def test_menu_has_about_60_items():
    # Phase 11 highway rebuild: 48 classics + 12 dishes researched from
    # 99 KM Coffee (millet veg) and Manna Mess (non-veg) on NH-45.
    assert 56 <= len(MENU_ITEMS) <= 64
    assert len(MENU_ITEMS) == 60


def test_every_scheduled_item_parses_and_meal_periods_match():
    """Hard windows must be well-formed and coherent with soft meal_periods."""
    from dosadash_shared import availability

    for item in MENU_ITEMS:
        if item.schedule is None:
            continue
        for day, entry in item.schedule.items():
            assert day in availability.WEEKDAYS, f"{item.name}: bad day {day}"
            for w in entry if isinstance(entry, list) else [entry]:
                availability._parse(w["start"]), availability._parse(w["end"])


def test_highway_signature_dishes_present():
    names = {i.name for i in MENU_ITEMS}
    # 99 KM Coffee (veg millet kitchen)
    assert {"Ragi Millet Dosa", "Kuzhi Paniyaram (7 pcs)", "Thinai Pongal", "Mini Tiffin"} <= names
    # Manna Mess (non-veg highway mess)
    assert {
        "Mutton Chukka",
        "Nattu Kozhi Kuzhambu",
        "Karuvadu Thokku",
        "Non-Veg Mess Meals",
    } <= names
    mess = [i for i in MENU_ITEMS if i.category == "Mess Specials"]
    assert mess and all(not i.is_veg for i in mess)
    millet = [i for i in MENU_ITEMS if i.category == "Millet Specials"]
    assert millet and all(i.is_veg for i in millet)


def test_dosa_not_available_at_lunch():
    """The feature's namesake: every Dosa-category dish is off-schedule at
    1 PM IST but on-schedule at breakfast and dinner."""
    from datetime import datetime

    from dosadash_shared.availability import IST, item_on_schedule

    lunch = datetime(2026, 8, 20, 13, 0, tzinfo=IST)
    breakfast = datetime(2026, 8, 20, 8, 0, tzinfo=IST)
    dinner = datetime(2026, 8, 20, 19, 30, tzinfo=IST)
    for dosa in (i for i in MENU_ITEMS if i.category == "Dosa"):
        assert not item_on_schedule(dosa.schedule, lunch), f"{dosa.name} served at lunch"
        assert item_on_schedule(dosa.schedule, breakfast)
        assert item_on_schedule(dosa.schedule, dinner)


def test_menu_ingredients_all_defined():
    validate_menu()  # raises on unknown ingredient


def test_every_item_has_valid_meal_periods():
    for item in MENU_ITEMS:
        assert item.meal_periods, f"{item.name}: no meal periods"
        assert set(item.meal_periods) <= set(MEAL_PERIODS), (
            f"{item.name}: invalid meal periods {item.meal_periods}"
        )


def test_dosa_prices_realistic():
    dosas = [i for i in MENU_ITEMS if i.category == "Dosa"]
    assert len(dosas) == 10
    assert all(Decimal("80") <= i.price <= Decimal("180") for i in dosas)


def test_allergen_mapping():
    cheese_dosa = next(i for i in MENU_ITEMS if i.name == "Cheese Dosa")
    assert "cheese" in item_allergens(cheese_dosa)
    lemon_rice = next(i for i in MENU_ITEMS if i.name == "Lemon Rice")
    assert "peanut" in item_allergens(lemon_rice)
    plain_dosa = next(i for i in MENU_ITEMS if i.name == "Plain Dosa")
    assert item_allergens(plain_dosa) == set()


def test_menu_names_unique():
    names = [i.name for i in MENU_ITEMS]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------- users


def test_500_users_unique_phones_deterministic():
    a = generate_users(500, seed=42)
    b = generate_users(500, seed=42)
    assert a == b
    assert len({u.phone for u in a}) == 500
    assert all(u.phone.startswith("+91") for u in a)


def test_personas_cover_diets():
    diets = {p.diet for p in PERSONAS}
    assert diets == {Diet.VEG, Diet.VEGAN, Diet.JAIN, Diet.NONVEG}


# --------------------------------------------------------------- seasonality


def test_pongal_multiplier():
    idli = next(i for i in MENU_ITEMS if i.name == "Idli (2 pcs)")
    pongal_day = date(2026, 1, 15)  # Thursday, Pongal window
    normal_day = date(2026, 1, 8)  # Thursday
    assert demand_multiplier(idli, pongal_day) == 3.0
    assert demand_multiplier(idli, normal_day) == 1.0


def test_weekend_biryani_spike():
    biryani = next(i for i in MENU_ITEMS if i.name == "Chicken Biryani")
    saturday = date(2026, 6, 6)
    tuesday = date(2026, 6, 2)
    assert demand_multiplier(biryani, saturday) == 2.5
    assert demand_multiplier(biryani, tuesday) == 1.0


def test_diwali_sweets_multiplier():
    sweet = next(i for i in MENU_ITEMS if i.category == "Sweets")
    assert demand_multiplier(sweet, date(2025, 10, 20)) == 4.0


# --------------------------------------------------------------------- orders


def test_orders_deterministic():
    users = generate_users(50, seed=7)
    a = generate_orders(users, days=30, end=date(2026, 6, 30), seed=7)
    b = generate_orders(users, days=30, end=date(2026, 6, 30), seed=7)
    assert a == b
    assert len(a) > 100  # 50 users * ~30 days produce a meaningful history


def test_delivery_minutes_bounded_with_real_signal():
    users = generate_users(100, seed=42)
    orders = generate_orders(users, days=60, end=date(2026, 6, 30), seed=42)
    minutes = [o.delivered_minutes for o in orders]
    assert all(18 <= m <= 90 for m in minutes)
    assert len(set(minutes)) > 10  # not a constant — real regression target
    # peak-hour orders take longer on average (ETA-model signal)
    peak = [o.delivered_minutes for o in orders if o.placed_at.hour in (12, 13, 19, 20, 21)]
    off = [o.delivered_minutes for o in orders if o.placed_at.hour not in (12, 13, 19, 20, 21)]
    assert sum(peak) / len(peak) > sum(off) / len(off)


def test_festival_day_calendar():
    assert is_festival_day(date(2026, 1, 15))  # Pongal window
    assert is_festival_day(date(2025, 10, 21))  # Diwali +1
    assert not is_festival_day(date(2026, 6, 3))


def test_category_multiplier_matches_item_multiplier():
    for item in MENU_ITEMS[:10]:
        for day in (date(2026, 1, 15), date(2026, 6, 6), date(2025, 10, 20)):
            assert demand_multiplier(item, day) == category_multiplier(
                item.category, item.is_veg, day
            )


def test_orders_respect_diet():
    users = generate_users(200, seed=42)
    veg_phones = {u.phone for u in users if u.persona.diet in (Diet.VEG, Diet.VEGAN, Diet.JAIN)}
    nonveg_items = {i.name for i in MENU_ITEMS if not i.is_veg}
    orders = generate_orders(users, days=60, end=date(2026, 6, 30), seed=42)
    for order in orders:
        if order.user_phone in veg_phones:
            assert not {line.item_name for line in order.items} & nonveg_items


def test_orders_respect_allergens():
    users = generate_users(500, seed=42)
    peanut_users = {u.phone for u in users if "peanut" in u.persona.allergens}
    assert peanut_users, "expected at least one peanut-allergy persona in 500 users"
    peanut_items = {i.name for i in MENU_ITEMS if "peanut" in item_allergens(i)}
    orders = generate_orders(users, days=60, end=date(2026, 6, 30), seed=42)
    for order in orders:
        if order.user_phone in peanut_users:
            assert not {line.item_name for line in order.items} & peanut_items
