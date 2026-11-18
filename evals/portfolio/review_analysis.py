"""Actual local confidence and serving-route audit; no threshold fitting."""

from dosadash_ai.routers.reviews import draft_from_local_labels, score_review_batch
from dosadash_ml.finetune.predict import load_sentiment_champion, predict_sentiment
from dosadash_shared import ReviewScoreRequest, ReviewScoreSourceItem

from .runner import ROOT, metrics


def local_analysis(cases):
    champion = load_sentiment_champion(ROOT / "packages/ml/artifacts")
    predictions = predict_sentiment(champion, [c.text for c in cases])
    rows = []
    for c, p in zip(cases, predictions, strict=True):
        accepted = p.confident and draft_from_local_labels(1, p.labels) is not None
        rows.append(
            {
                "id": c.id,
                "labels": list(p.labels),
                "confidence_flag": p.confident,
                "legacy_local_accepted": accepted,
                "metrics": metrics(c, p.labels),
                "missing": sorted(set(c.expected) - set(p.labels)),
                "extra": sorted(set(p.labels) - set(c.expected)),
            }
        )
    return {
        "model": champion.version,
        "cases": rows,
        "legacy_confident_errors": sum(
            r["legacy_local_accepted"] and not r["metrics"]["exact_match"] for r in rows
        ),
        "legacy_accepted": sum(r["legacy_local_accepted"] for r in rows),
        "note": "Synthetic diagnostic cases; confidence is not calibrated correctness. No fitting.",
    }


async def hybrid_prediction(case):
    result = await score_review_batch(
        ReviewScoreRequest(
            reviews=[ReviewScoreSourceItem(review_id=1, text=case.text, rating=case.rating)]
        )
    )
    if result.rejected or len(result.scores) != 1:
        raise RuntimeError("serving route omitted review")
    labels = [f"{a.aspect}:{a.sentiment}" for a in result.scores[0].aspects]
    route = "local" if 1 in result.local_ids else "llm"
    return labels, (result.local_model if route == "local" else result.model)
