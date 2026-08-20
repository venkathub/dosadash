"""Phase 8 shared benchmark metrics — one scorer for both benchmark sides."""

from dosadash_ml.finetune.dataset import Example, build_examples
from dosadash_ml.finetune.metrics import label_set_metrics

EXAMPLES = [e for e in build_examples(users=80, days=90, seed=11) if e.split == "test"][:50]


def test_perfect_predictions_score_one():
    preds = [set(e.labels) for e in EXAMPLES]
    m = label_set_metrics(preds, EXAMPLES)
    assert m["micro_f1"] == 1.0
    assert m["macro_f1"] == 1.0
    assert m["exact_match"] == 1.0
    assert m["sentiment_accuracy"] == 1.0


def test_empty_predictions_score_zero_f1():
    preds = [set() for _ in EXAMPLES]
    m = label_set_metrics(preds, EXAMPLES)
    assert m["micro_f1"] == 0.0
    assert m["exact_match"] == 0.0
    # rollup of an empty set is NONE — never accidentally correct
    assert m["sentiment_accuracy"] == 0.0


def test_partial_credit_is_between():
    half = [set(list(e.labels)[:1]) for e in EXAMPLES]  # first label only
    m = label_set_metrics(half, EXAMPLES)
    assert 0.0 < m["micro_f1"] <= 1.0


def test_off_label_predictions_are_penalized():
    preds = [set(e.labels) | {"taste:NEGATIVE"} for e in EXAMPLES]
    m = label_set_metrics(preds, EXAMPLES)
    assert m["micro_f1"] < 1.0
    assert m["exact_match"] < 1.0


def test_sentiment_rollup_mixed_detection():
    e = next(x for x in EXAMPLES if x.sentiment == "POSITIVE" and len(x.labels) == 1)
    wrong = set(e.labels) | {f"{next(iter(e.labels)).rsplit(':', 1)[0]}:NEGATIVE"}
    # adding the opposite polarity flips the rollup to MIXED → sentiment miss
    m = label_set_metrics([wrong], [e])
    assert m["sentiment_accuracy"] == 0.0


def test_length_mismatch_rejected():
    try:
        label_set_metrics([], EXAMPLES)
    except AssertionError:
        return
    raise AssertionError("expected AssertionError on length mismatch")


def test_examples_pool_nonempty():
    assert len(EXAMPLES) >= 20
    assert all(isinstance(e, Example) for e in EXAMPLES)
