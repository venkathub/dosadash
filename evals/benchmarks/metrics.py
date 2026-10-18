"""Group-bootstrap intervals and paired deltas; no automatic quality/promotion gate."""

from collections import defaultdict
from random import Random

from dosadash_ml.finetune.dataset import Example, rollup
from dosadash_ml.finetune.metrics import label_set_metrics

METRICS = ("micro_f1", "macro_f1", "exact_match", "sentiment_accuracy")


def examples(records):
    return [
        Example(
            text="",
            labels=tuple(r.resolved_labels()),
            sentiment=rollup(r.resolved_labels()),
            language=r.language,
            rating=r.rating,
            split=r.split,
        )
        for r in records
    ]


def score(records, predictions):
    return label_set_metrics([set(p["labels"]) for p in predictions], examples(records))


def samples(records, repeats, seed):
    groups = defaultdict(list)
    for i, record in enumerate(records):
        groups[record.group_id].append(i)
    members = list(groups.values())
    rng = Random(seed)
    for _ in range(repeats):
        yield [i for group in rng.choices(members, k=len(members)) for i in group]


def interval(values):
    values = sorted(values)
    return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]


def summary(records, predictions, *, repeats, seed):
    errors = sum(p["status"] != "ok" for p in predictions)
    groups = len({r.group_id for r in records})
    result = {"n": len(records), "groups": groups, "errors": errors, "metrics": None, "ci95": None}
    if not records or errors:
        result["warning"] = "Incomplete predictions: quality metrics are unavailable."
        return result
    result["metrics"] = score(records, predictions)
    if groups >= 2:
        draws = [
            score([records[i] for i in indices], [predictions[i] for i in indices])
            for indices in samples(records, repeats, seed)
        ]
        result["ci95"] = {name: interval([draw[name] for draw in draws]) for name in METRICS}
    if groups < 30:
        result["warning"] = "Fewer than 30 independent groups; intervals may be unstable."
    return result


def report_metrics(records, predictions, *, repeats, seed):
    if len(records) != len(predictions):
        raise ValueError("predictions must cover every record")
    result = {"overall": summary(records, predictions, repeats=repeats, seed=seed)}
    for dimension in ("language", "category"):
        groups = defaultdict(list)
        for i, record in enumerate(records):
            keys = [record.language] if dimension == "language" else record.categories
            for key in keys:
                groups[key].append(i)
        result[dimension] = {
            key: summary(
                [records[i] for i in indices],
                [predictions[i] for i in indices],
                repeats=repeats,
                seed=seed,
            )
            for key, indices in sorted(groups.items())
        }
    return result


def paired_delta(records, left, right, *, repeats, seed):
    if any(p["status"] != "ok" for p in [*left, *right]):
        return {"available": False, "reason": "incomplete paired predictions"}
    a, b = score(records, left), score(records, right)
    draws = []
    if len({r.group_id for r in records}) >= 2:
        for indices in samples(records, repeats, seed):
            subset = [records[i] for i in indices]
            x = score(subset, [left[i] for i in indices])
            y = score(subset, [right[i] for i in indices])
            draws.append({name: x[name] - y[name] for name in METRICS})
    return {
        "available": True,
        "direction": "onnx minus llm",
        "delta": {name: a[name] - b[name] for name in METRICS},
        "ci95": {name: interval([d[name] for d in draws]) for name in METRICS} if draws else None,
        "method": "paired percentile bootstrap resampling source groups, not individual rows",
    }
