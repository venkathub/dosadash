"""Synthetic A/B test for the checkout suggester (Phase 7 deliverable:
"checkout combo suggester (synthetic A/B uplift)").

    uv run python -m dosadash_ml.recsys.absim [--out packages/ml/artifacts/recsys/ab_sim.json]

Three arms over the holdout-window checkouts of the synthetic world:

- **control**       — no suggestion shown (baseline AOV)
- **random**        — a random diet-eligible item from the pairing categories
- **personalized**  — `suggest_addons` ranked by ALS fold-in on each user's
                      training-window history (cold users → popularity order),
                      i.e. exactly what apps/ai serves

Ground truth: the generating personas. A shopper accepts a suggested item
with probability BASE_ACCEPT scaled by their persona's affinity for the
item's category (and never accepts diet/allergen-violating items — the
same `_allowed` rule that generated the data). The personalized arm must
beat random on attach rate and control on AOV; those gates are enforced in
CI against the committed metrics file.

Honest caveat, recorded in the output: acceptance is simulated from the
same personas that generated the training data — this measures that the
pipeline recovers persona taste end-to-end, not real-world uplift.
"""

import argparse
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from random import Random

from dosadash_ml.datagen import MENU_ITEMS, generate_orders, generate_users
from dosadash_ml.datagen.menu import SeedMenuItem
from dosadash_ml.datagen.orders import _allowed
from dosadash_ml.datagen.users import SyntheticUser
from dosadash_ml.recsys.predict import (
    RecsysChampion,
    load_recsys_champion,
    recommend_from_history,
)
from dosadash_ml.recsys.suggest import PAIRING_CATEGORIES, SuggestCandidate, suggest_addons

BASE_ACCEPT = 0.35  # acceptance probability at maximum persona affinity


@dataclass(frozen=True)
class ArmResult:
    sessions: int
    attaches: int
    revenue: float  # cart revenue + accepted add-ons

    @property
    def attach_rate(self) -> float:
        return self.attaches / self.sessions if self.sessions else 0.0

    @property
    def aov(self) -> float:
        return self.revenue / self.sessions if self.sessions else 0.0


def _accept_probability(user: SyntheticUser, item: SeedMenuItem) -> float:
    """Persona ground truth: affinity-scaled base rate, 0 for diet violations."""
    if not _allowed(item, user):
        return 0.0
    weights = user.persona.category_weights
    w_max = max(weights.values())
    return BASE_ACCEPT * weights.get(item.category, 0.0) / w_max


def simulate(
    *,
    users: int = 500,
    days: int = 365,
    valid_days: int = 28,
    seed: int = 42,
    model_dir: str | Path = "packages/ml/artifacts",
) -> dict:
    champion: RecsysChampion = load_recsys_champion(model_dir)
    population = generate_users(n=users, seed=seed)
    by_phone = {u.phone: u for u in population}
    items_by_name = {m.name: m for m in MENU_ITEMS}
    orders = generate_orders(population, days=days, seed=seed)
    cutoff = (date.today() - timedelta(days=1)) - timedelta(days=valid_days)

    # Training-window history per user (what prod would read from the DB)
    history: dict[str, dict[str, float]] = {}
    checkouts = []
    for order in orders:
        if order.placed_at.date() <= cutoff:
            counts = history.setdefault(order.user_phone, {})
            for line in order.items:
                counts[line.item_name] = counts.get(line.item_name, 0.0) + float(line.qty)
        else:
            checkouts.append(order)

    popularity_rank = {name: rank for rank, name in enumerate(champion.popularity)}
    rng = {arm: Random(seed + i) for i, arm in enumerate(("random", "personalized"))}
    totals = {arm: [0, 0, 0.0] for arm in ("control", "random", "personalized")}

    for order in checkouts:
        user = by_phone[order.user_phone]
        cart_names = {line.item_name for line in order.items}
        cart_value = float(
            sum(items_by_name[line.item_name].price * line.qty for line in order.items)
        )
        cart_categories = {items_by_name[n].category for n in cart_names}
        eligible = [m for m in MENU_ITEMS if m.name not in cart_names and _allowed(m, user)]

        for arm in ("control", "random", "personalized"):
            totals[arm][0] += 1
            totals[arm][2] += cart_value

        # random arm: any eligible pairing-category item
        pool = [m for m in eligible if m.category in PAIRING_CATEGORIES]
        if pool:
            pick = rng["random"].choice(pool)
            if rng["random"].random() < _accept_probability(user, pick):
                totals["random"][1] += 1
                totals["random"][2] += float(pick.price)

        # personalized arm: the real suggester on ALS fold-in scores
        counts = history.get(order.user_phone, {})
        ranked = recommend_from_history(
            champion, counts, k=len(eligible), allowed={m.name for m in eligible}
        )
        if ranked is not None:
            scores = dict(ranked)
        else:  # cold user → popularity order as scores
            scores = {m.name: -popularity_rank.get(m.name, len(popularity_rank)) for m in eligible}
        candidates = [
            SuggestCandidate(name=m.name, category=m.category, score=scores.get(m.name, -1e9))
            for m in eligible
        ]
        suggestions = suggest_addons(
            cart_names, cart_categories, candidates, combos=[], max_suggestions=1
        )
        if suggestions:
            item = items_by_name[suggestions[0].item_name]
            if rng["personalized"].random() < _accept_probability(user, item):
                totals["personalized"][1] += 1
                totals["personalized"][2] += float(item.price)

    arms = {arm: ArmResult(sessions=t[0], attaches=t[1], revenue=t[2]) for arm, t in totals.items()}
    control, random_arm, personalized = arms["control"], arms["random"], arms["personalized"]
    return {
        "config": {
            "users": users,
            "days": days,
            "valid_days": valid_days,
            "seed": seed,
            "base_accept": BASE_ACCEPT,
            "model_version": champion.version,
        },
        "sessions": control.sessions,
        "attach_rate_random": round(random_arm.attach_rate, 4),
        "attach_rate_personalized": round(personalized.attach_rate, 4),
        "aov_control": round(control.aov, 2),
        "aov_random": round(random_arm.aov, 2),
        "aov_personalized": round(personalized.aov, 2),
        "aov_uplift_vs_control_pct": round(100 * (personalized.aov / control.aov - 1), 2),
        "aov_uplift_vs_random_pct": round(100 * (personalized.aov / random_arm.aov - 1), 2),
        "caveat": (
            "Acceptance is simulated from the personas that generated the training "
            "data; this validates end-to-end taste recovery, not real-world uplift."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic A/B sim for the checkout suggester")
    parser.add_argument("--users", type=int, default=500)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--valid-days", type=int, default=28)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model-dir", default="packages/ml/artifacts")
    parser.add_argument("--out", default="packages/ml/artifacts/recsys/ab_sim.json")
    args = parser.parse_args()
    result = simulate(
        users=args.users,
        days=args.days,
        valid_days=args.valid_days,
        seed=args.seed,
        model_dir=args.model_dir,
    )
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"{result['sessions']} checkouts · attach {result['attach_rate_personalized']:.1%} "
        f"(random {result['attach_rate_random']:.1%}) · AOV ₹{result['aov_personalized']} "
        f"vs control ₹{result['aov_control']} "
        f"(+{result['aov_uplift_vs_control_pct']}%; "
        f"vs random +{result['aov_uplift_vs_random_pct']}%)"
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
