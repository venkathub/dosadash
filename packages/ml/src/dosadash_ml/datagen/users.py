"""Synthetic users with taste personas (~500, deterministic).

Personas drive both preference rows in the DB and item selection in
`orders.py` — this coupling is what later makes the recommender and
forecasting models learn real structure from the synthetic history.
"""

from dataclasses import dataclass
from random import Random

from dosadash_shared import Diet

FIRST_NAMES = (
    "Arun",
    "Priya",
    "Karthik",
    "Divya",
    "Suresh",
    "Meena",
    "Vignesh",
    "Lakshmi",
    "Rajesh",
    "Anitha",
    "Hari",
    "Kavya",
    "Manoj",
    "Revathi",
    "Senthil",
    "Deepa",
    "Ganesh",
    "Nithya",
    "Prakash",
    "Shalini",
    "Bala",
    "Janani",
    "Ashwin",
    "Keerthi",
    "Mohan",
    "Pavithra",
    "Naveen",
    "Swathi",
    "Ramesh",
    "Aishwarya",
)
LAST_NAMES = (
    "Kumar",
    "Raj",
    "Iyer",
    "Murugan",
    "Pillai",
    "Nair",
    "Reddy",
    "Subramanian",
    "Krishnan",
    "Venkatesh",
    "Shetty",
    "Naidu",
)


@dataclass(frozen=True)
class Persona:
    key: str
    diet: Diet
    # category -> relative weight when picking items
    category_weights: dict[str, float]
    orders_per_week: float
    spice_level: int
    allergens: tuple[str, ...] = ()


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "veg_family",
        Diet.VEG,
        {
            "Dosa": 3,
            "Idli & Vada": 3,
            "Uttapam": 2,
            "Rice & Pongal": 2,
            "Sweets": 1,
            "Beverages": 1,
            "Snacks": 1,
        },
        orders_per_week=1.6,
        spice_level=1,
    ),
    Persona(
        "spice_lover",
        Diet.NONVEG,
        {"Chettinad Curry": 4, "Biryani": 3, "Dosa": 2, "Snacks": 2},
        orders_per_week=1.2,
        spice_level=3,
    ),
    Persona(
        "biryani_weekender",
        Diet.NONVEG,
        {"Biryani": 5, "Snacks": 2, "Beverages": 1, "Chettinad Curry": 1},
        orders_per_week=0.9,
        spice_level=2,
    ),
    Persona(
        "filter_coffee_daily",
        Diet.VEG,
        {"Beverages": 5, "Idli & Vada": 2, "Dosa": 2, "Sweets": 1},
        orders_per_week=3.5,
        spice_level=1,
    ),
    Persona(
        "vegan_health",
        Diet.VEGAN,
        {"Dosa": 3, "Idli & Vada": 2, "Rice & Pongal": 2, "Uttapam": 2},
        orders_per_week=1.0,
        spice_level=1,
        allergens=("milk", "curd", "ghee"),
    ),
    Persona(
        "jain_traditional",
        Diet.JAIN,
        {"Idli & Vada": 3, "Dosa": 3, "Rice & Pongal": 2, "Sweets": 2, "Beverages": 1},
        orders_per_week=1.1,
        spice_level=0,
    ),
    Persona(
        "sweet_tooth",
        Diet.VEG,
        {"Sweets": 4, "Beverages": 2, "Dosa": 2, "Idli & Vada": 1},
        orders_per_week=1.3,
        spice_level=0,
    ),
    Persona(
        "late_night_snacker",
        Diet.NONVEG,
        {"Snacks": 4, "Dosa": 2, "Biryani": 2, "Beverages": 1},
        orders_per_week=1.4,
        spice_level=2,
    ),
    Persona(
        "peanut_allergy_careful",
        Diet.VEG,
        {"Dosa": 3, "Idli & Vada": 3, "Uttapam": 2, "Beverages": 1},
        orders_per_week=1.0,
        spice_level=1,
        allergens=("peanut", "cashew"),
    ),
    Persona(
        "balanced_everything",
        Diet.NONVEG,
        {
            "Dosa": 2,
            "Idli & Vada": 2,
            "Biryani": 2,
            "Rice & Pongal": 2,
            "Chettinad Curry": 1,
            "Snacks": 1,
            "Beverages": 1,
            "Sweets": 1,
            "Uttapam": 1,
        },
        orders_per_week=1.2,
        spice_level=2,
    ),
)

_PERSONA_WEIGHTS = (18, 10, 14, 8, 6, 6, 8, 10, 4, 16)  # relative frequency
_LANGUAGES = ("en", "en", "en", "ta", "ta", "hi")  # ~50/33/17


@dataclass(frozen=True)
class SyntheticUser:
    phone: str
    name: str
    persona: Persona
    language: str


def generate_users(n: int = 500, seed: int = 42) -> list[SyntheticUser]:
    """Deterministic user population; same (n, seed) → identical output."""
    rng = Random(seed)
    users: list[SyntheticUser] = []
    for i in range(n):
        persona = rng.choices(PERSONAS, weights=_PERSONA_WEIGHTS, k=1)[0]
        users.append(
            SyntheticUser(
                phone=f"+9198{10000000 + i:08d}",
                name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                persona=persona,
                language=rng.choice(_LANGUAGES),
            )
        )
    return users
