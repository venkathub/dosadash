"""Shared metrics for aspect-sentiment predictions (no torch imports).

One implementation used by BOTH sides of the benchmark — LoRA and zero-shot
predictions are reduced to label SETS first, so neither path can be scored
by different math.
"""

from statistics import mean

from dosadash_ml.finetune.dataset import LABELS, Example, rollup


def label_set_metrics(preds: list[set[str]], examples: list[Example]) -> dict[str, float]:
    """micro/macro F1 + exact match + rollup sentiment accuracy over label
    sets. Labels never seen in truth nor predictions are skipped for macro
    (absent ≠ free credit)."""
    assert len(preds) == len(examples)
    truth_sets = [set(e.labels) for e in examples]

    tp = sum(len(p & t) for p, t in zip(preds, truth_sets, strict=True))
    fp = sum(len(p - t) for p, t in zip(preds, truth_sets, strict=True))
    fn = sum(len(t - p) for p, t in zip(preds, truth_sets, strict=True))
    micro_p = tp / (tp + fp) if tp + fp else 0.0
    micro_r = tp / (tp + fn) if tp + fn else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if micro_p + micro_r else 0.0

    per_label_f1: list[float] = []
    for label in LABELS:
        tpj = sum(1 for p, t in zip(preds, truth_sets, strict=True) if label in p and label in t)
        fpj = sum(
            1 for p, t in zip(preds, truth_sets, strict=True) if label in p and label not in t
        )
        fnj = sum(
            1 for p, t in zip(preds, truth_sets, strict=True) if label not in p and label in t
        )
        if tpj + fpj + fnj == 0:
            continue  # label absent from this split — skip, don't reward
        pj = tpj / (tpj + fpj) if tpj + fpj else 0.0
        rj = tpj / (tpj + fnj) if tpj + fnj else 0.0
        per_label_f1.append(2 * pj * rj / (pj + rj) if pj + rj else 0.0)

    exact = sum(1 for p, t in zip(preds, truth_sets, strict=True) if p == t) / len(examples)
    sentiment_hits = sum(rollup(p) == e.sentiment for p, e in zip(preds, examples, strict=True))
    return {
        "micro_f1": round(float(micro_f1), 6),
        "macro_f1": round(float(mean(per_label_f1)), 6),
        "exact_match": round(float(exact), 6),
        "sentiment_accuracy": round(sentiment_hits / len(examples), 6),
        "examples": float(len(examples)),
    }
