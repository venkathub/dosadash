"""Generate `knowledge/allergens.md` from the canonical menu seed.

The allergen/dietary guide is GENERATED, never hand-edited: menu.py is the
single source of truth, so the RAG knowledge base can never drift from the
seeded menu (Hard Rule 4 in spirit — no stale allergen claims).

Usage:
    python -m dosadash_ml.datagen.knowledge          # rewrite the file
    python -m dosadash_ml.datagen.knowledge --check  # exit 1 if stale (CI/test)
"""

import argparse
import sys
from pathlib import Path

from dosadash_ml.datagen.menu import INGREDIENTS, MENU_ITEMS, SeedMenuItem, item_allergens

# Allergen ingredient → customer-facing allergen group.
ALLERGEN_GROUPS: dict[str, str] = {
    "semolina (rava)": "gluten",
    "maida (wheat flour)": "gluten",
    "milk": "dairy",
    "curd": "dairy",
    "ghee": "dairy",
    "butter": "dairy",
    "paneer": "dairy",
    "cheese": "dairy",
    "peanut": "peanut",
    "cashew": "tree nut (cashew)",
    "mustard seeds": "mustard",
    "egg": "egg",
    "dry fish (karuvadu)": "fish",
    "crab": "shellfish",
    "prawn": "shellfish",
}

_ANIMAL_PRODUCTS = {name for name, group in ALLERGEN_GROUPS.items() if group in ("dairy", "egg")}
_SPICE_LABELS = ("no spice", "mild", "medium", "hot")


def diet_label(item: SeedMenuItem) -> str:
    """non-veg | vegan | veg — vegan derives from the recipe mapping."""
    if not item.is_veg:
        return "non-veg"
    if set(item.ingredients) & _ANIMAL_PRODUCTS:
        return "veg"
    return "vegan"


def _allergen_cell(item: SeedMenuItem) -> str:
    groups = sorted({ALLERGEN_GROUPS[a] for a in item_allergens(item)})
    return ", ".join(groups) if groups else "none"


def render_allergen_guide() -> str:
    """Full markdown document (front-matter + legend + per-category tables)."""
    lines = [
        "---",
        "title: Allergen & Dietary Guide",
        "doc_type: allergen_guide",
        "tags: [allergens, dietary, veg, vegan, jain, spice]",
        "generated_by: python -m dosadash_ml.datagen.knowledge",
        "---",
        "",
        "# Allergen & Dietary Guide",
        "",
        "> GENERATED from the canonical menu seed — do not edit by hand.",
        "> Regenerate with `python -m dosadash_ml.datagen.knowledge`.",
        "",
        "## How to read this guide",
        "",
        "- **Allergen groups** used on this menu: "
        + ", ".join(sorted(set(ALLERGEN_GROUPS.values())))
        + ".",
        "- **Diet**: `vegan` items contain no meat, egg, or dairy; `veg` items are",
        "  vegetarian but contain dairy; `non-veg` items contain meat or egg.",
        "- **Jain-friendly** items are vegetarian AND prepared without onion or garlic.",
        "- **Spice**: no spice → mild → medium → hot. Spice level can be adjusted on",
        "  request for most cooked-to-order dishes; ask when ordering.",
        "- All fried items share a common fryer; trace cross-contact between dishes",
        "  is possible. Severe-allergy customers should mention it in order notes.",
        "",
    ]

    categories: list[str] = []
    for item in MENU_ITEMS:
        if item.category not in categories:
            categories.append(item.category)

    for category in categories:
        lines += [
            f"## {category}",
            "",
            "| Item | Diet | Jain-friendly | Spice | Allergens |",
            "|---|---|---|---|---|",
        ]
        for item in MENU_ITEMS:
            if item.category != category:
                continue
            jain = "yes" if item.is_veg and not item.contains_onion_garlic else "no"
            lines.append(
                f"| {item.name} | {diet_label(item)} | {jain} | "
                f"{_SPICE_LABELS[item.spice_level]} | {_allergen_cell(item)} |"
            )
        lines.append("")

    return "\n".join(lines)


def default_output_path() -> Path:
    """Repo-root-relative knowledge/allergens.md (generator lives in packages/ml)."""
    return Path(__file__).resolve().parents[4].parent / "knowledge" / "allergens.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate knowledge/allergens.md")
    parser.add_argument("--check", action="store_true", help="fail if file is stale")
    parser.add_argument("--out", type=Path, default=default_output_path())
    args = parser.parse_args()

    rendered = render_allergen_guide() + "\n"
    if args.check:
        current = args.out.read_text() if args.out.exists() else ""
        if current != rendered:
            print(f"STALE: {args.out} — run `python -m dosadash_ml.datagen.knowledge`")
            sys.exit(1)
        print(f"ok: {args.out} is up to date")
        return
    args.out.write_text(rendered)
    print(f"wrote {args.out}")


# Sanity: every allergen ingredient must have a customer-facing group.
_missing = {i.name for i in INGREDIENTS if i.is_allergen} - set(ALLERGEN_GROUPS)
if _missing:  # pragma: no cover — import-time guard
    raise RuntimeError(f"allergen ingredients missing a group label: {_missing}")


if __name__ == "__main__":
    main()
