"""Guards the generated knowledge base against drift from the menu seed.

`knowledge/allergens.md` is generated from `dosadash_ml.datagen.menu` — the
single source of truth. If someone edits the menu without regenerating (or
hand-edits the generated file), these tests fail.
"""

from dosadash_ml.datagen.knowledge import (
    ALLERGEN_GROUPS,
    default_output_path,
    diet_label,
    render_allergen_guide,
)
from dosadash_ml.datagen.menu import INGREDIENTS, MENU_ITEMS


def test_committed_allergen_guide_is_up_to_date():
    path = default_output_path()
    assert path.exists(), f"missing {path} — run `python -m dosadash_ml.datagen.knowledge`"
    assert path.read_text() == render_allergen_guide() + "\n", (
        "knowledge/allergens.md is stale — run `python -m dosadash_ml.datagen.knowledge`"
    )


def test_every_allergen_ingredient_has_a_group():
    allergens = {i.name for i in INGREDIENTS if i.is_allergen}
    assert allergens == set(ALLERGEN_GROUPS)


def test_every_menu_item_appears_in_guide():
    guide = render_allergen_guide()
    for item in MENU_ITEMS:
        assert f"| {item.name} |" in guide


def test_diet_labels():
    by_name = {i.name: i for i in MENU_ITEMS}
    assert diet_label(by_name["Plain Dosa"]) == "vegan"
    assert diet_label(by_name["Ghee Roast Dosa"]) == "veg"  # ghee = dairy
    assert diet_label(by_name["Chicken Biryani"]) == "non-veg"
    assert diet_label(by_name["Egg Biryani"]) == "non-veg"


def test_known_allergen_rows():
    guide = render_allergen_guide()
    assert "| Cheese Dosa | veg | yes | mild | dairy |" in guide
    assert "| Lemon Rice | vegan | yes | mild | mustard, peanut |" in guide
    assert "| Plain Dosa | vegan | yes | mild | none |" in guide
    assert "| Rava Idli | veg | yes | mild | dairy, gluten, mustard, tree nut (cashew) |" in guide
