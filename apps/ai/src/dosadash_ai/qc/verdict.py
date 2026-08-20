"""Deterministic QC verdict from VLM observations (never from the model).

Verdict ladder (first match wins):

- UNREADABLE — the vision chain failed entirely (staff retakes the photo)
- CHECK      — not recognizably a food photo, or too blurry to trust
- MISMATCH   — an ordered dish is not visible in the photo
- CHECK      — dishes match but there are visible presentation issues
- PASS       — everything ordered is visible, nothing looks wrong

Name matching reuses the invoice matcher's idea: max(sequence similarity,
token containment) so "masala dosa" matches "dosa with masala filling"
while "idli" vs "payasam" stays at 0.
"""

import re
from difflib import SequenceMatcher

from dosadash_shared import DishQCExtraction, DishQCResult

MATCH_THRESHOLD = 0.5
MIN_PHOTO_CONFIDENCE = 0.3  # below this the photo itself is the problem


def _normalize(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    # strip pack-size noise: "idli (2 pcs)" → "idli"
    cleaned = re.sub(r"\b\d+\b|\bpcs?\b", " ", cleaned)
    # crude singularization so "two idlis" matches "Idli (2 pcs)"
    tokens = [t[:-1] if t.endswith("s") and len(t) > 3 else t for t in cleaned.split()]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    containment = len(ta & tb) / min(len(ta), len(tb))
    return round(max(seq, containment), 3)


def compute_result(
    expected: list[str],
    extraction: DishQCExtraction | None,
    *,
    model: str | None = None,
    error: str | None = None,
) -> DishQCResult:
    if extraction is None:
        return DishQCResult(
            verdict="UNREADABLE",
            missing=list(expected),
            issues=["Photo could not be analyzed — please retake"],
            model=model,
            error=error,
        )

    if not extraction.is_food_photo or extraction.confidence < MIN_PHOTO_CONFIDENCE:
        return DishQCResult(
            verdict="CHECK",
            missing=list(expected),
            issues=["Not recognizable as a clear food photo — please retake"],
            extraction=extraction,
            model=model,
        )

    matched, missing = [], []
    used_seen: set[int] = set()
    for dish in expected:
        best_idx, best_score = None, 0.0
        for idx, seen in enumerate(extraction.dishes_seen):
            score = name_similarity(dish, seen)
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx is not None and best_score >= MATCH_THRESHOLD:
            matched.append(dish)
            used_seen.add(best_idx)
        else:
            missing.append(dish)
    unexpected = [
        seen
        for idx, seen in enumerate(extraction.dishes_seen)
        if idx not in used_seen
        and all(name_similarity(seen, dish) < MATCH_THRESHOLD for dish in expected)
    ]

    issues = list(extraction.presentation_issues)
    if missing:
        verdict = "MISMATCH"
    elif issues:
        verdict = "CHECK"
    else:
        verdict = "PASS"
    return DishQCResult(
        verdict=verdict,
        matched=matched,
        missing=missing,
        unexpected=unexpected,
        issues=issues,
        extraction=extraction,
        model=model,
    )
