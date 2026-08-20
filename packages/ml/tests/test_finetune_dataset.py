"""Phase 8 fine-tune dataset: determinism, split hygiene, label space."""

from dosadash_ml.finetune.dataset import (
    LABELS,
    build_examples,
    multi_hot,
    rollup,
)
from dosadash_shared import REVIEW_ASPECTS

EXAMPLES = build_examples(users=120, days=120, seed=7)


def test_label_space_is_registry_times_polarity():
    assert len(LABELS) == len(REVIEW_ASPECTS) * 2
    for label in LABELS:
        aspect, polarity = label.rsplit(":", 1)
        assert aspect in REVIEW_ASPECTS
        assert polarity in ("POSITIVE", "NEGATIVE")


def test_deterministic():
    assert build_examples(users=120, days=120, seed=7) == EXAMPLES


def test_split_proportions_and_disjointness():
    splits = {s: [e for e in EXAMPLES if e.split == s] for s in ("train", "val", "test")}
    n = len(EXAMPLES)
    assert sum(len(v) for v in splits.values()) == n
    assert 0.7 <= len(splits["train"]) / n <= 0.9
    assert len(splits["val"]) / n >= 0.05
    assert len(splits["test"]) / n >= 0.05


def test_no_empty_texts_and_every_example_labeled():
    for e in EXAMPLES:
        assert e.text
        assert e.labels, e.text  # text reviews always carry planted aspects
        assert set(e.labels) <= set(LABELS)


def test_multi_hot_round_trip():
    e = EXAMPLES[0]
    row = multi_hot(e.labels)
    assert sum(row) == len(e.labels)
    recovered = {LABELS[i] for i, v in enumerate(row) if v}
    assert recovered == set(e.labels)


def test_rollup_matches_planted_sentiment():
    """The dataset rollup rule must agree with datagen's planted rollup —
    the serving guardrail uses the same rule, so all three stay in lockstep."""
    for e in EXAMPLES:
        assert rollup(e.labels) == e.sentiment, e.text


def test_split_assignment_is_text_independent():
    """Split hangs off the order index, not the text: editing templates in
    datagen must never migrate examples across the train/test boundary
    (which would silently contaminate the benchmark holdout)."""
    again = build_examples(users=120, days=120, seed=7)
    for a, b in zip(EXAMPLES, again, strict=True):
        assert a.split == b.split
