"""Key-free CI gates for the INT8 ONNX sentiment serving artifact (Phase 8
slice 4).

The committed artifact under packages/ml/artifacts/sentiment/onnx/ is what
the ai service actually serves — these gates re-verify it on every merge,
through the EXACT serving code path (`dosadash_ml.finetune.predict`):

- parity: INT8 must stay within a bounded gap of the fp32 champion, and a
  LIVE recompute on a deterministic holdout sample must clear the floor —
  a corrupt/stale committed binary fails CI, not production
- the confidence contract must earn its keep: confident predictions must be
  MORE accurate than the overall INT8 numbers (that is why unconfident ones
  escalate to the LLM), and coverage must stay high enough that the ₹0
  serving story holds
- label-space drift between the registry, the champion and the ONNX meta
  fails loudly; predictions can never leave the registry by construction —
  asserted anyway
- artifact size guard: an accidental fp32 commit fails before it bloats git

No torch, no keys: onnxruntime + tokenizers only (the `sentiment` extra the
ai service itself uses).
"""

import json
from pathlib import Path

import numpy as np
import pytest

from dosadash_ml.finetune.dataset import LABELS, build_examples
from dosadash_ml.finetune.metrics import label_set_metrics
from dosadash_ml.finetune.predict import (
    CONFIDENCE_MARGIN,
    is_confident,
    load_sentiment_champion,
    predict_sentiment,
)

ARTIFACTS = Path(__file__).resolve().parents[2] / "packages" / "ml" / "artifacts"
SENTIMENT = ARTIFACTS / "sentiment"
ONNX_DIR = SENTIMENT / "onnx"

MACRO_F1_FLOOR = 0.95
MAX_QUANTIZATION_DELTA = 0.02  # INT8 may trail fp32 by at most 2 macro-F1 pts
MIN_CONFIDENT_COVERAGE = 0.70  # the ₹0 story requires the bulk to stay local
MAX_INT8_BYTES = 90 * 1024 * 1024  # fp32 DistilBERT is ~265 MB — catches a bad commit
RECOMPUTE_SAMPLE = 200


def _meta() -> dict:
    return json.loads((ONNX_DIR / "meta.json").read_text())


def _champion_meta() -> dict:
    return json.loads((SENTIMENT / "champion" / "meta.json").read_text())


def test_artifact_exists_and_meta_parses():
    meta = _meta()
    assert (ONNX_DIR / "model.int8.onnx").exists()
    assert (ONNX_DIR / "tokenizer.json").exists()
    assert meta["model_version"].endswith("-int8")


def test_label_space_matches_registry_and_champion():
    meta = _meta()
    champ = _champion_meta()
    assert meta["labels"] == list(LABELS)
    assert meta["labels"] == champ["labels"]
    assert meta["threshold"] == champ["threshold"]
    assert meta["model_version"] == f"{champ['model_version']}-int8"


def test_recorded_parity_floors():
    meta = _meta()
    assert meta["int8_macro_f1"] >= MACRO_F1_FLOOR, meta["int8_macro_f1"]
    assert meta["fp32_macro_f1"] - meta["int8_macro_f1"] <= MAX_QUANTIZATION_DELTA
    assert meta["confident_coverage"] >= MIN_CONFIDENT_COVERAGE
    # the confidence rule must earn its keep: what serves locally is at
    # least as accurate as the overall INT8 numbers
    assert meta["confident_macro_f1"] >= meta["int8_macro_f1"]


def test_artifact_size_guard():
    size = (ONNX_DIR / "model.int8.onnx").stat().st_size
    assert size == _meta()["int8_bytes"], "meta drifted from the committed binary"
    assert size <= MAX_INT8_BYTES, f"{size} bytes — did an fp32 model get committed?"


def test_confidence_margin_coherence():
    """The margin recorded at export must be the constant the ai service
    serves with (same prompt↔constant coherence pattern as the agents)."""
    assert _meta()["confidence_margin"] == CONFIDENCE_MARGIN
    assert 0.0 < CONFIDENCE_MARGIN < 0.5


def test_is_confident_invariants():
    threshold = _meta()["threshold"]
    n = len(LABELS)
    assert is_confident(np.full(n, 0.99), threshold)
    # empty predicted set (everything below threshold) is never confident
    assert not is_confident(np.full(n, 0.01), threshold)
    # any probability in the ambiguity band → not confident
    probs = np.full(n, 0.99)
    probs[0] = threshold + CONFIDENCE_MARGIN / 2
    assert not is_confident(probs, threshold)


def test_live_recompute_on_holdout_sample():
    """Load the COMMITTED binary through the serving path and re-score a
    deterministic sample of the shared test split — parity is proven on
    every merge, not just trusted from meta.json."""
    try:
        champion = load_sentiment_champion(ARTIFACTS)
    except ImportError:  # pragma: no cover — env without the sentiment extra
        pytest.skip("onnxruntime/tokenizers not installed")
    test = [e for e in build_examples() if e.split == "test"][:RECOMPUTE_SAMPLE]
    predictions = predict_sentiment(champion, [e.text for e in test])

    for p in predictions:  # predictions can never leave the registry
        assert set(p.labels) <= set(LABELS)

    metrics = label_set_metrics([set(p.labels) for p in predictions], test)
    assert metrics["macro_f1"] >= MACRO_F1_FLOOR, metrics
    coverage = sum(p.confident for p in predictions) / len(predictions)
    assert coverage >= 0.6, coverage  # sample-level slack vs the full-test floor
