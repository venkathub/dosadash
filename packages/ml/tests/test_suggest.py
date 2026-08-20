"""Checkout suggester tests: pure rules + synthetic A/B artifact gates.

The A/B gates lock the deliverable's story in CI: the personalized arm must
beat random on attach rate and control on AOV. (These caught the first
implementation's fixed category priority losing to random — see suggest.py.)
"""

import json
from pathlib import Path

import pytest

from dosadash_ml.recsys.suggest import (
    PAIRING_CATEGORIES,
    ComboDef,
    SuggestCandidate,
    Suggestion,
    suggest_addons,
)

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"

CANDIDATES = [
    SuggestCandidate("Filter Coffee", "Beverages", 0.9),
    SuggestCandidate("Rose Milk", "Beverages", 0.4),
    SuggestCandidate("Semiya Payasam", "Sweets", 1.5),
    SuggestCandidate("Medu Vada (2 pcs)", "Snacks", 0.2),
    SuggestCandidate("Ghee Roast Dosa", "Dosa", 2.0),  # not an attach category
]


def test_pairing_picks_best_score_across_gap_categories():
    out = suggest_addons({"Masala Dosa"}, {"Dosa"}, CANDIDATES)
    # payasam (1.5) outranks coffee (0.9) — the score picks the category too
    assert [s.item_name for s in out] == ["Semiya Payasam", "Filter Coffee"]
    assert all(s.kind == "pairing" for s in out)
    # never suggests from a non-attach category, no matter the score
    assert "Ghee Roast Dosa" not in [s.item_name for s in out]


def test_one_suggestion_per_category():
    out = suggest_addons({"Masala Dosa"}, {"Dosa"}, CANDIDATES, max_suggestions=3)
    beverages = [s for s in out if s.item_name in ("Filter Coffee", "Rose Milk")]
    assert len(beverages) == 1  # Rose Milk never rides along with Filter Coffee


def test_categories_already_in_cart_are_skipped():
    out = suggest_addons({"Masala Dosa", "Filter Coffee"}, {"Dosa", "Beverages"}, CANDIDATES)
    assert all(s.item_name not in ("Filter Coffee", "Rose Milk") for s in out)


def test_combo_completion_outranks_pairing():
    combos = [ComboDef("Dosa + Coffee Combo", ("Masala Dosa", "Filter Coffee"))]
    out = suggest_addons({"Masala Dosa"}, {"Dosa"}, CANDIDATES, combos)
    assert out[0] == Suggestion("Filter Coffee", "combo", "Completes the Dosa + Coffee Combo")


def test_combo_needs_exactly_one_missing_item():
    combos = [ComboDef("Big Tiffin", ("Masala Dosa", "Filter Coffee", "Semiya Payasam"))]
    out = suggest_addons({"Masala Dosa"}, {"Dosa"}, CANDIDATES, combos)  # 2 missing
    assert all(s.kind == "pairing" for s in out)
    out = suggest_addons(
        {"Masala Dosa", "Filter Coffee"}, {"Dosa", "Beverages"}, CANDIDATES, combos
    )
    assert out[0].kind == "combo" and out[0].item_name == "Semiya Payasam"


def test_empty_cart_suggests_nothing():
    assert suggest_addons(set(), set(), CANDIDATES) == []


def test_deterministic():
    a = suggest_addons({"Masala Dosa"}, {"Dosa"}, CANDIDATES)
    b = suggest_addons({"Masala Dosa"}, {"Dosa"}, list(reversed(CANDIDATES)))
    assert a == b


# ------------------------------------------------- synthetic A/B sim gates


def _ab_metrics() -> dict:
    return json.loads((ARTIFACTS / "recsys" / "ab_sim.json").read_text())


def test_ab_sim_personalized_beats_random_attach():
    m = _ab_metrics()
    assert m["attach_rate_personalized"] > m["attach_rate_random"], (
        "personalized suggestions must out-attach random ones — re-run "
        "`python -m dosadash_ml.recsys.absim` after suggester/model changes"
    )


def test_ab_sim_positive_aov_uplift():
    m = _ab_metrics()
    assert m["aov_uplift_vs_control_pct"] > 0
    assert m["aov_personalized"] > m["aov_control"]
    assert m["sessions"] > 1000  # enough checkouts to mean something


def test_ab_sim_reproducible_small():
    """Fresh tiny runs are deterministic (same seed → identical metrics)."""
    pytest.importorskip("scipy")
    from dosadash_ml.recsys.absim import simulate

    a = simulate(users=80, days=120, valid_days=14, seed=7, model_dir=ARTIFACTS)
    b = simulate(users=80, days=120, valid_days=14, seed=7, model_dir=ARTIFACTS)
    assert a == b
    assert set(PAIRING_CATEGORIES) == {"Beverages", "Sweets", "Snacks"}
