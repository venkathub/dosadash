"""Fine-tune dataset: planted-label synthetic reviews → multi-label examples.

Label space is exactly (shared aspect registry × polarity) = 16 labels —
eval-gate coherence with the datagen templates and the LLM guardrail.

Split is a deterministic hash of the review's order index (80/10/10
train/val/test): the TEST split is the benchmark holdout shared by the LoRA
model and the zero-shot LLM baseline, so the accuracy-vs-cost comparison is
apples-to-apples on reviews neither path ever trained on. No torch imports
here — the dataset builder stays importable in the base CI environment.
"""

from dataclasses import dataclass
from hashlib import sha256

from dosadash_ml.datagen import generate_orders, generate_reviews, generate_users
from dosadash_shared import REVIEW_ASPECTS

LABELS: tuple[str, ...] = tuple(
    f"{aspect}:{polarity}" for aspect in REVIEW_ASPECTS for polarity in ("POSITIVE", "NEGATIVE")
)
LABEL_INDEX: dict[str, int] = {label: i for i, label in enumerate(LABELS)}

_TRAIN_BUCKETS = frozenset(range(8))  # 80%
_VAL_BUCKET = 8
_TEST_BUCKET = 9


@dataclass(frozen=True)
class Example:
    text: str
    labels: tuple[str, ...]  # subset of LABELS (planted ground truth)
    sentiment: str  # POSITIVE | NEGATIVE | MIXED rollup
    language: str  # en | hinglish | tanglish
    rating: int
    split: str  # train | val | test


def _bucket(order_index: int, seed: int) -> int:
    digest = sha256(f"review-split:{seed}:{order_index}".encode()).digest()
    return digest[0] % 10


def _split(order_index: int, seed: int) -> str:
    bucket = _bucket(order_index, seed)
    if bucket in _TRAIN_BUCKETS:
        return "train"
    return "val" if bucket == _VAL_BUCKET else "test"


def build_examples(*, users: int = 500, days: int = 365, seed: int = 42) -> list[Example]:
    """Deterministic labeled examples. Empty-text (rating-only) reviews are
    excluded — there is nothing for a text model to learn from them and the
    serving path scores them deterministically anyway."""
    synth_users = generate_users(n=users, seed=seed)
    orders = generate_orders(synth_users, days=days, seed=seed)
    reviews = generate_reviews(synth_users, orders, seed=seed)
    examples: list[Example] = []
    for r in reviews:
        if not r.text:
            continue
        labels = tuple(sorted(f"{a.aspect}:{a.sentiment}" for a in r.aspects))
        examples.append(
            Example(
                text=r.text,
                labels=labels,
                sentiment=r.sentiment,
                language=r.language,
                rating=r.rating,
                split=_split(r.order_index, seed),
            )
        )
    return examples


def multi_hot(labels: tuple[str, ...]) -> list[float]:
    row = [0.0] * len(LABELS)
    for label in labels:
        row[LABEL_INDEX[label]] = 1.0
    return row


def rollup(labels: set[str] | tuple[str, ...]) -> str:
    """Deterministic overall sentiment from predicted labels — the same
    rollup rule the serving guardrail applies to LLM output."""
    polarities = {label.rsplit(":", 1)[1] for label in labels}
    if polarities == {"POSITIVE"}:
        return "POSITIVE"
    if polarities == {"NEGATIVE"}:
        return "NEGATIVE"
    return "MIXED" if polarities else "NONE"
