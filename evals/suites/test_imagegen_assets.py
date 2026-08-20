"""Key-free CI gates for menu image generation (Phase 7, Hard Rule 5).

Images can't be judged without a provider, so the gates pin the CONTRACT:
the versioned prompt must carry the non-negotiable style rules (no text in
the frame, veg dishes never show meat, no people), the prompt builder must
put the dish facts — and nothing else — in front of the model, and the
result schema must refuse junk. The approval-only publish path (nothing
reaches menu_items.image_url without a human) is enforced end-to-end in
apps/api/tests/test_admin_menu_images_api.py.
"""

from dosadash_ai.prompts import load_prompt
from dosadash_ai.routers.imagegen import build_prompt
from dosadash_shared import MENU_IMAGE_PROMPT_VERSION, MenuImageRequest


def _req(**overrides) -> MenuImageRequest:
    base = dict(
        item_name="Masala Dosa",
        category="Dosa",
        description="Crisp dosa with potato masala",
        is_veg=True,
    )
    base.update(overrides)
    return MenuImageRequest(**base)


def test_prompt_carries_the_style_contract():
    prompt = load_prompt(MENU_IMAGE_PROMPT_VERSION)
    assert "NO text" in prompt  # no synthetic menus/price tags in the frame
    assert "watermarks" in prompt
    assert "hands or people" in prompt
    assert "Never depict meat, fish or egg in a vegetarian dish" in prompt
    assert "South Indian" in prompt  # domain grounding


def test_build_prompt_round_trips_dish_facts():
    prompt = build_prompt(_req())
    assert prompt.startswith(load_prompt(MENU_IMAGE_PROMPT_VERSION))
    assert "Dish: Masala Dosa" in prompt
    assert "Category: Dosa" in prompt
    assert "Crisp dosa with potato masala" in prompt
    assert "Vegetarian: yes — strictly no meat, fish or egg" in prompt


def test_build_prompt_states_non_veg_and_skips_missing_description():
    prompt = build_prompt(_req(item_name="Chicken Biryani", is_veg=False, description=None))
    assert "Vegetarian: no" in prompt
    assert "Description:" not in prompt


def test_prompt_never_mentions_prices():
    """Prices are not part of the visual language — a leaked ₹ invites the
    model to render price tags, which the style contract forbids."""
    assert "₹" not in build_prompt(_req())


def test_result_schema_refuses_junk():
    import pytest
    from pydantic import ValidationError

    from dosadash_shared import MenuImageResult

    with pytest.raises(ValidationError):  # too short to be a real image
        MenuImageResult(image_b64="abc", model="dall-e-3", prompt="p")
