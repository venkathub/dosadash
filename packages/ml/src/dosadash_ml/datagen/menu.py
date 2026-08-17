"""Canonical ~50-item South Indian menu seed with ingredient/allergen mapping.

Single source of truth for the DB seed (`dosadash_api.seed`) and datagen.
Prices are realistic INR (dosa ₹80–180, docs/CLAUDE.md domain notes).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

MEAL_PERIODS: tuple[str, ...] = ("breakfast", "lunch", "snacks", "dinner")

# Hard serving window for pongal dishes: breakfast counters only (06:00–12:00 IST).
_MORNING_ONLY: dict[str, dict[str, str]] = {
    day: {"start": "06:00", "end": "12:00"}
    for day in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
}


@dataclass(frozen=True)
class SeedIngredient:
    name: str
    unit: str
    is_allergen: bool = False


@dataclass(frozen=True)
class SeedMenuItem:
    name: str
    category: str
    price: Decimal
    description: str
    is_veg: bool = True
    contains_onion_garlic: bool = True  # False → Jain-friendly
    spice_level: int = 1  # 0–3
    prep_minutes: int = 15
    ingredients: tuple[str, ...] = field(default_factory=tuple)
    # Every item MUST declare ≥1 period from MEAL_PERIODS (validate_menu enforces).
    meal_periods: tuple[str, ...] = field(default_factory=tuple)
    # Optional hard serving window ({day: {start, end}}); None = always served.
    schedule: dict[str, Any] | None = None


INGREDIENTS: tuple[SeedIngredient, ...] = (
    SeedIngredient("idli rice", "kg"),
    SeedIngredient("urad dal", "kg"),
    SeedIngredient("toor dal", "kg"),
    SeedIngredient("chana dal", "kg"),
    SeedIngredient("semolina (rava)", "kg", is_allergen=True),  # gluten
    SeedIngredient("maida (wheat flour)", "kg", is_allergen=True),  # gluten
    SeedIngredient("milk", "l", is_allergen=True),  # dairy
    SeedIngredient("curd", "kg", is_allergen=True),  # dairy
    SeedIngredient("ghee", "kg", is_allergen=True),  # dairy
    SeedIngredient("butter", "kg", is_allergen=True),  # dairy
    SeedIngredient("paneer", "kg", is_allergen=True),  # dairy
    SeedIngredient("cheese", "kg", is_allergen=True),  # dairy
    SeedIngredient("peanut", "kg", is_allergen=True),
    SeedIngredient("cashew", "kg", is_allergen=True),
    SeedIngredient("mustard seeds", "kg", is_allergen=True),
    SeedIngredient("egg", "pc", is_allergen=True),
    SeedIngredient("chicken", "kg"),
    SeedIngredient("mutton", "kg"),
    SeedIngredient("potato", "kg"),
    SeedIngredient("onion", "kg"),
    SeedIngredient("tomato", "kg"),
    SeedIngredient("green chilli", "kg"),
    SeedIngredient("dried red chilli", "kg"),
    SeedIngredient("black pepper", "kg"),
    SeedIngredient("curry leaves", "bunch"),
    SeedIngredient("coriander leaves", "bunch"),
    SeedIngredient("coconut", "pc"),
    SeedIngredient("tamarind", "kg"),
    SeedIngredient("lemon", "pc"),
    SeedIngredient("seeraga samba rice", "kg"),
    SeedIngredient("basmati rice", "kg"),
    SeedIngredient("jaggery", "kg"),
    SeedIngredient("sugar", "kg"),
    SeedIngredient("coffee powder", "kg"),
    SeedIngredient("tea powder", "kg"),
    SeedIngredient("rose syrup", "l"),
    SeedIngredient("chettinad masala", "kg"),
    SeedIngredient("sambar powder", "kg"),
    SeedIngredient("idli podi", "kg"),
    SeedIngredient("okra", "kg"),
    SeedIngredient("moong dal", "kg"),
    SeedIngredient("vegetable oil", "l"),
    SeedIngredient("rice flour", "kg"),
)

_D = Decimal

# meal_periods shorthands (subsets of MEAL_PERIODS)
_BF_DIN = ("breakfast", "dinner")
_LUN_DIN = ("lunch", "dinner")
_BF_ONLY = ("breakfast",)
_LUN_ONLY = ("lunch",)
_SNACKS_ONLY = ("snacks",)
_SN_DIN = ("snacks", "dinner")
_BF_SN = ("breakfast", "snacks")
_ALL_DAY = MEAL_PERIODS

MENU_ITEMS: tuple[SeedMenuItem, ...] = (
    # ------------------------------------------------------------- Dosas (10)
    SeedMenuItem(
        "Plain Dosa",
        "Dosa",
        _D("90"),
        "Crisp golden classic from fermented rice-lentil batter",
        contains_onion_garlic=False,
        ingredients=("idli rice", "urad dal", "vegetable oil"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Masala Dosa",
        "Dosa",
        _D("120"),
        "Crisp dosa with spiced potato-onion masala",
        spice_level=1,
        prep_minutes=18,
        ingredients=("idli rice", "urad dal", "potato", "onion", "mustard seeds", "curry leaves"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Mysore Masala Dosa",
        "Dosa",
        _D("140"),
        "Fiery red chutney smeared, potato masala inside",
        spice_level=2,
        prep_minutes=18,
        ingredients=("idli rice", "urad dal", "potato", "onion", "dried red chilli", "chana dal"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Ghee Roast Dosa",
        "Dosa",
        _D("150"),
        "Extra-crisp cone roasted in generous ghee",
        contains_onion_garlic=False,
        prep_minutes=20,
        ingredients=("idli rice", "urad dal", "ghee"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Podi Dosa",
        "Dosa",
        _D("110"),
        "Dosa dusted with gunpowder podi and gingelly oil",
        spice_level=2,
        ingredients=("idli rice", "urad dal", "idli podi", "vegetable oil"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Onion Dosa",
        "Dosa",
        _D("100"),
        "Dosa layered with caramelised onions and chillies",
        spice_level=1,
        ingredients=("idli rice", "urad dal", "onion", "green chilli"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Rava Dosa",
        "Dosa",
        _D("130"),
        "Lacy instant semolina crepe with cumin and pepper",
        contains_onion_garlic=False,
        prep_minutes=20,
        ingredients=("semolina (rava)", "black pepper", "curry leaves", "green chilli"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Set Dosa",
        "Dosa",
        _D("110"),
        "Soft spongy dosas, set of three, with chutney",
        contains_onion_garlic=False,
        ingredients=("idli rice", "urad dal", "coconut"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Cheese Dosa",
        "Dosa",
        _D("150"),
        "Molten cheese folded into a crisp dosa",
        contains_onion_garlic=False,
        ingredients=("idli rice", "urad dal", "cheese", "butter"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Kal Dosa",
        "Dosa",
        _D("100"),
        "Thick soft griddle dosa, street-style",
        contains_onion_garlic=False,
        ingredients=("idli rice", "urad dal", "vegetable oil"),
        meal_periods=_BF_DIN,
    ),
    # ------------------------------------------------------- Idli & Vada (6)
    SeedMenuItem(
        "Idli (2 pcs)",
        "Idli & Vada",
        _D("80"),
        "Steamed fluffy idlis with sambar and chutneys",
        contains_onion_garlic=False,
        prep_minutes=10,
        ingredients=("idli rice", "urad dal", "sambar powder", "toor dal"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Mini Idli Sambar",
        "Idli & Vada",
        _D("100"),
        "Button idlis soaked in ghee-tempered sambar",
        prep_minutes=12,
        ingredients=("idli rice", "urad dal", "toor dal", "sambar powder", "ghee"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Podi Idli",
        "Idli & Vada",
        _D("110"),
        "Idlis tossed in spicy gunpowder podi",
        spice_level=2,
        prep_minutes=12,
        ingredients=("idli rice", "urad dal", "idli podi", "vegetable oil"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Medu Vada (2 pcs)",
        "Idli & Vada",
        _D("90"),
        "Golden crisp lentil doughnuts",
        prep_minutes=12,
        ingredients=("urad dal", "black pepper", "curry leaves", "onion", "vegetable oil"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Curd Vada",
        "Idli & Vada",
        _D("110"),
        "Vadas soaked in seasoned whipped curd",
        contains_onion_garlic=False,
        prep_minutes=10,
        ingredients=("urad dal", "curd", "mustard seeds", "coriander leaves"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Rava Idli",
        "Idli & Vada",
        _D("100"),
        "Karnataka-style semolina idli with cashew",
        contains_onion_garlic=False,
        prep_minutes=14,
        ingredients=("semolina (rava)", "curd", "cashew", "mustard seeds"),
        meal_periods=_BF_DIN,
    ),
    # ------------------------------------------------------------ Uttapam (3)
    SeedMenuItem(
        "Onion Uttapam",
        "Uttapam",
        _D("120"),
        "Thick griddle cake topped with onions",
        prep_minutes=18,
        ingredients=("idli rice", "urad dal", "onion", "green chilli"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Tomato Onion Uttapam",
        "Uttapam",
        _D("130"),
        "Uttapam with tangy tomato-onion topping",
        prep_minutes=18,
        ingredients=("idli rice", "urad dal", "tomato", "onion"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Podi Uttapam",
        "Uttapam",
        _D("130"),
        "Uttapam finished with roasted gunpowder podi",
        spice_level=2,
        prep_minutes=18,
        ingredients=("idli rice", "urad dal", "idli podi"),
        meal_periods=_BF_DIN,
    ),
    # ------------------------------------------------------ Rice & Pongal (6)
    SeedMenuItem(
        "Ven Pongal",
        "Rice & Pongal",
        _D("110"),
        "Comforting rice-moong pongal, pepper and ghee",
        contains_onion_garlic=False,
        prep_minutes=15,
        ingredients=("idli rice", "moong dal", "black pepper", "ghee", "cashew"),
        meal_periods=_BF_DIN,
        schedule=_MORNING_ONLY,
    ),
    SeedMenuItem(
        "Sweet Pongal",
        "Rice & Pongal",
        _D("120"),
        "Jaggery pongal with cashew and ghee",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=15,
        ingredients=("idli rice", "moong dal", "jaggery", "ghee", "cashew"),
        meal_periods=_BF_DIN,
        schedule=_MORNING_ONLY,
    ),
    SeedMenuItem(
        "Curd Rice",
        "Rice & Pongal",
        _D("100"),
        "Cooling curd rice with pomegranate tempering",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=10,
        ingredients=("idli rice", "curd", "mustard seeds", "curry leaves"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Lemon Rice",
        "Rice & Pongal",
        _D("100"),
        "Zesty turmeric-lemon rice with peanuts",
        contains_onion_garlic=False,
        prep_minutes=12,
        ingredients=("idli rice", "lemon", "peanut", "mustard seeds", "curry leaves"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Tamarind Rice",
        "Rice & Pongal",
        _D("110"),
        "Tangy puliyodarai with roasted peanuts",
        contains_onion_garlic=False,
        spice_level=2,
        prep_minutes=12,
        ingredients=("idli rice", "tamarind", "peanut", "dried red chilli"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Sambar Rice",
        "Rice & Pongal",
        _D("120"),
        "Hearty one-pot sambar sadam with ghee",
        prep_minutes=15,
        ingredients=("idli rice", "toor dal", "sambar powder", "ghee", "okra"),
        meal_periods=_LUN_DIN,
    ),
    # ------------------------------------------------------------ Biryani (4)
    SeedMenuItem(
        "Chicken Biryani",
        "Biryani",
        _D("220"),
        "Dindigul-style seeraga samba chicken biryani",
        is_veg=False,
        spice_level=2,
        prep_minutes=30,
        ingredients=("seeraga samba rice", "chicken", "onion", "curd", "chettinad masala"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Mutton Biryani",
        "Biryani",
        _D("280"),
        "Slow-cooked mutton biryani, seeraga samba",
        is_veg=False,
        spice_level=2,
        prep_minutes=35,
        ingredients=("seeraga samba rice", "mutton", "onion", "curd", "chettinad masala"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Egg Biryani",
        "Biryani",
        _D("190"),
        "Masala-tossed eggs over fragrant biryani rice",
        is_veg=False,
        spice_level=2,
        prep_minutes=25,
        ingredients=("seeraga samba rice", "egg", "onion", "chettinad masala"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Veg Biryani",
        "Biryani",
        _D("180"),
        "Seasonal vegetables and basmati, mint raita",
        spice_level=2,
        prep_minutes=25,
        ingredients=("basmati rice", "onion", "tomato", "curd", "chettinad masala"),
        meal_periods=_LUN_DIN,
    ),
    # -------------------------------------------------- Chettinad Curries (4)
    SeedMenuItem(
        "Chicken Chettinad",
        "Chettinad Curry",
        _D("240"),
        "Fiery pepper-fennel chicken curry",
        is_veg=False,
        spice_level=3,
        prep_minutes=25,
        ingredients=("chicken", "chettinad masala", "black pepper", "onion", "coconut"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Pepper Mutton",
        "Chettinad Curry",
        _D("290"),
        "Dry-roasted mutton in cracked pepper",
        is_veg=False,
        spice_level=3,
        prep_minutes=30,
        ingredients=("mutton", "black pepper", "onion", "curry leaves"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Kara Kuzhambu",
        "Chettinad Curry",
        _D("160"),
        "Tangy tamarind curry with okra",
        spice_level=3,
        prep_minutes=20,
        ingredients=("tamarind", "okra", "sambar powder", "onion", "vegetable oil"),
        meal_periods=_LUN_DIN,
    ),
    SeedMenuItem(
        "Paneer Chettinad",
        "Chettinad Curry",
        _D("190"),
        "Paneer in pepper-coconut masala",
        spice_level=2,
        prep_minutes=20,
        ingredients=("paneer", "chettinad masala", "coconut", "onion"),
        meal_periods=_LUN_DIN,
    ),
    # ------------------------------------------------------------- Snacks (4)
    SeedMenuItem(
        "Aloo Bonda (3 pcs)",
        "Snacks",
        _D("80"),
        "Spiced potato fritters in gram batter",
        prep_minutes=12,
        ingredients=("potato", "onion", "green chilli", "vegetable oil"),
        meal_periods=_SNACKS_ONLY,
    ),
    SeedMenuItem(
        "Onion Bajji (4 pcs)",
        "Snacks",
        _D("80"),
        "Crisp onion fritters for rainy evenings",
        prep_minutes=12,
        ingredients=("onion", "green chilli", "vegetable oil"),
        meal_periods=_SNACKS_ONLY,
    ),
    SeedMenuItem(
        "Parotta (2 pcs)",
        "Snacks",
        _D("120"),
        "Flaky layered parottas with salna",
        prep_minutes=15,
        ingredients=("maida (wheat flour)", "vegetable oil", "onion"),
        meal_periods=_SN_DIN,
    ),
    SeedMenuItem(
        "Kothu Parotta",
        "Snacks",
        _D("160"),
        "Chopped parotta stir-fried with egg and masala",
        is_veg=False,
        spice_level=2,
        prep_minutes=18,
        ingredients=("maida (wheat flour)", "egg", "onion", "tomato", "chettinad masala"),
        meal_periods=_SN_DIN,
    ),
    # ------------------------------------------------------------- Sweets (3)
    SeedMenuItem(
        "Rava Kesari",
        "Sweets",
        _D("90"),
        "Saffron semolina kesari with ghee and cashew",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=10,
        ingredients=("semolina (rava)", "sugar", "ghee", "cashew"),
        meal_periods=_SNACKS_ONLY,
    ),
    SeedMenuItem(
        "Mysore Pak",
        "Sweets",
        _D("100"),
        "Melt-in-mouth gram flour and ghee classic",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=8,
        ingredients=("chana dal", "sugar", "ghee"),
        meal_periods=_SNACKS_ONLY,
    ),
    SeedMenuItem(
        "Semiya Payasam",
        "Sweets",
        _D("110"),
        "Vermicelli kheer with cashew and cardamom",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=12,
        ingredients=("maida (wheat flour)", "milk", "sugar", "cashew", "ghee"),
        meal_periods=_SNACKS_ONLY,
    ),
    # ---------------------------------------------------------- Beverages (4)
    SeedMenuItem(
        "Filter Coffee",
        "Beverages",
        _D("60"),
        "Frothy degree coffee in davara-tumbler",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=5,
        ingredients=("coffee powder", "milk", "sugar"),
        meal_periods=_ALL_DAY,
    ),
    SeedMenuItem(
        "Masala Chai",
        "Beverages",
        _D("50"),
        "Spiced milk tea, kadak",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=5,
        ingredients=("tea powder", "milk", "sugar"),
        meal_periods=_ALL_DAY,
    ),
    SeedMenuItem(
        "Neer Mor",
        "Beverages",
        _D("40"),
        "Spiced buttermilk with curry leaves",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=5,
        ingredients=("curd", "green chilli", "curry leaves"),
        meal_periods=_ALL_DAY,
    ),
    SeedMenuItem(
        "Rose Milk",
        "Beverages",
        _D("70"),
        "Chilled rose milk, Madurai-style",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=5,
        ingredients=("milk", "rose syrup", "sugar"),
        meal_periods=_ALL_DAY,
    ),
    # ------------------------------------------------------------- Tiffin (6)
    SeedMenuItem(
        "Idiyappam (3 pcs)",
        "Tiffin",
        _D("100"),
        "Steamed string hoppers with sweet coconut milk",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=15,
        ingredients=("rice flour", "coconut"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Appam",
        "Tiffin",
        _D("110"),
        "Lacy-edged fermented rice hopper, soft centre",
        contains_onion_garlic=False,
        spice_level=0,
        prep_minutes=15,
        ingredients=("idli rice", "coconut"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Poori Masala (2 pcs)",
        "Tiffin",
        _D("110"),
        "Puffed golden pooris with potato-onion masala",
        spice_level=1,
        prep_minutes=15,
        ingredients=("maida (wheat flour)", "potato", "onion"),
        meal_periods=_BF_ONLY,
    ),
    SeedMenuItem(
        "Adai Avial",
        "Tiffin",
        _D("130"),
        "Protein-rich mixed-dal adai with coconut avial",
        spice_level=2,
        prep_minutes=20,
        ingredients=("toor dal", "chana dal", "urad dal", "coconut", "curd"),
        meal_periods=_BF_DIN,
    ),
    SeedMenuItem(
        "Lemon Sevai",
        "Tiffin",
        _D("90"),
        "Tangy rice-noodle sevai with peanuts and lemon",
        contains_onion_garlic=False,
        spice_level=1,
        prep_minutes=12,
        ingredients=("rice flour", "lemon", "peanut", "mustard seeds"),
        meal_periods=_BF_SN,
    ),
    SeedMenuItem(
        "Rava Kitchadi",
        "Tiffin",
        _D("100"),
        "Soft ghee-laced semolina kitchadi with vegetables",
        spice_level=1,
        prep_minutes=15,
        ingredients=("semolina (rava)", "ghee", "onion"),
        meal_periods=_BF_ONLY,
    ),
    # -------------------------------------------------------------- Meals (2)
    SeedMenuItem(
        "South Indian Veg Meals",
        "Meals",
        _D("180"),
        "Full banana-leaf style meals: rice, sambar, poriyal, curd",
        spice_level=1,
        prep_minutes=20,
        ingredients=("idli rice", "toor dal", "sambar powder", "curd", "okra"),
        meal_periods=_LUN_ONLY,
    ),
    SeedMenuItem(
        "Vatha Kuzhambu Rice",
        "Meals",
        _D("120"),
        "Fiery tamarind vatha kuzhambu over steamed rice",
        spice_level=2,
        prep_minutes=15,
        ingredients=("idli rice", "tamarind", "sambar powder"),
        meal_periods=_LUN_DIN,
    ),
)

_INGREDIENT_NAMES = {i.name for i in INGREDIENTS}
_ALLERGEN_NAMES = {i.name for i in INGREDIENTS if i.is_allergen}


def item_allergens(item: SeedMenuItem) -> set[str]:
    """Allergen ingredient names for a menu item (drives the allergen KB)."""
    return set(item.ingredients) & _ALLERGEN_NAMES


def validate_menu() -> None:
    """Raise if any item references an undefined ingredient or lacks a valid
    meal-period assignment (used by tests/seed)."""
    for item in MENU_ITEMS:
        unknown = set(item.ingredients) - _INGREDIENT_NAMES
        if unknown:
            raise ValueError(f"{item.name}: unknown ingredients {unknown}")
        if not item.meal_periods:
            raise ValueError(f"{item.name}: must declare at least one meal period")
        invalid = set(item.meal_periods) - set(MEAL_PERIODS)
        if invalid:
            raise ValueError(f"{item.name}: invalid meal periods {invalid}")
