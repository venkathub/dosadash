"""CPU serving of the INT8 ONNX sentiment champion (Phase 8 slice 4).

Loads the quantized artifact exported by `export_onnx.py` and predicts
(aspect × polarity) label sets — numpy + onnxruntime + the Rust `tokenizers`
lib only. torch/transformers/peft stay in the root `finetune` dependency
group (recsys precedent: heavy training deps never reach the VPS images).

The SAME functions here are used by the export-time parity evaluation and
by the ai service at serve time, so the accuracy recorded in the artifact's
meta.json is measured through the exact code path that serves it.

Confidence contract (deterministic, no model self-assessment — dish-QC
philosophy): a prediction is CONFIDENT iff no label probability falls inside
the ambiguity band (threshold ± CONFIDENCE_MARGIN) AND at least one label
clears the threshold. Unconfident reviews escalate to the LLM path — the
tiny model handles the bulk at ₹0, the LLM handles the doubt.
"""

from dataclasses import dataclass
from json import loads
from pathlib import Path
from typing import Any

import numpy as np

CONFIDENCE_MARGIN = 0.15  # ambiguity band half-width around the threshold
ONNX_DIRNAME = "onnx"
ONNX_MODEL_FILE = "model.int8.onnx"
ONNX_META_FILE = "meta.json"
ONNX_TOKENIZER_FILE = "tokenizer.json"


@dataclass(frozen=True)
class SentimentPrediction:
    """One review's predicted label set + the deterministic confidence flag."""

    labels: tuple[str, ...]  # sorted "<aspect>:<POLARITY>" labels ≥ threshold
    confident: bool


@dataclass
class SentimentChampion:
    session: Any  # onnxruntime.InferenceSession
    tokenizer: Any  # tokenizers.Tokenizer
    labels: tuple[str, ...]
    threshold: float
    max_len: int
    version: str  # e.g. "dosadash-sentiment/v2-int8"


def is_confident(probs: np.ndarray, threshold: float, margin: float = CONFIDENCE_MARGIN) -> bool:
    """True iff no probability sits in the ambiguity band and ≥1 label fires.
    An empty predicted set is NEVER confident: the review has text, so a
    model that saw nothing gets a second opinion from the LLM."""
    if bool(np.any(np.abs(probs - threshold) < margin)):
        return False
    return bool(np.any(probs >= threshold))


def load_sentiment_champion(model_dir: str | Path) -> SentimentChampion:
    """Load the INT8 ONNX artifact. Lazy heavy imports so `dosadash_ml`
    stays importable without the sentiment extra."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    onnx_dir = Path(model_dir) / "sentiment" / ONNX_DIRNAME
    meta = loads((onnx_dir / ONNX_META_FILE).read_text())

    options = ort.SessionOptions()
    options.intra_op_num_threads = 2  # 4 GB VPS: never fan out across all cores
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(onnx_dir / ONNX_MODEL_FILE),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )

    max_len = int(meta["max_len"])
    tokenizer = Tokenizer.from_file(str(onnx_dir / ONNX_TOKENIZER_FILE))
    tokenizer.enable_truncation(max_length=max_len)
    tokenizer.enable_padding(
        length=max_len, pad_id=int(meta["pad_id"]), pad_token=meta["pad_token"]
    )

    return SentimentChampion(
        session=session,
        tokenizer=tokenizer,
        labels=tuple(meta["labels"]),
        threshold=float(meta["threshold"]),
        max_len=max_len,
        version=meta["model_version"],
    )


def predict_probs(
    champion: SentimentChampion, texts: list[str], *, batch_size: int = 16
) -> np.ndarray:
    """Sigmoid label probabilities, shape (len(texts), n_labels)."""
    if not texts:
        return np.zeros((0, len(champion.labels)), dtype=np.float32)
    rows: list[np.ndarray] = []
    for start in range(0, len(texts), batch_size):
        batch = champion.tokenizer.encode_batch(texts[start : start + batch_size])
        input_ids = np.array([e.ids for e in batch], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in batch], dtype=np.int64)
        (logits,) = champion.session.run(
            None, {"input_ids": input_ids, "attention_mask": attention_mask}
        )
        rows.append(1.0 / (1.0 + np.exp(-logits.astype(np.float64))))
    return np.concatenate(rows)


def predict_sentiment(
    champion: SentimentChampion, texts: list[str], *, batch_size: int = 16
) -> list[SentimentPrediction]:
    """Label sets + confidence for a batch of review texts."""
    probs = predict_probs(champion, texts, batch_size=batch_size)
    out: list[SentimentPrediction] = []
    for row in probs:
        labels = tuple(
            sorted(champion.labels[j] for j in np.flatnonzero(row >= champion.threshold))
        )
        out.append(
            SentimentPrediction(labels=labels, confident=is_confident(row, champion.threshold))
        )
    return out
