"""Export the LoRA sentiment champion → INT8 ONNX CPU-serving artifact.

    uv run --group finetune python -m dosadash_ml.finetune.export_onnx

Pipeline: committed champion adapter → merge LoRA into the DistilBERT base
→ ONNX export → dynamic INT8 quantization (onnxruntime) → parity evaluation
on the SAME held-out test split, run through the EXACT serving code path
(`dosadash_ml.finetune.predict`) the ai service uses — the numbers recorded
in meta.json are the numbers production serves.

The artifact (model + tokenizer.json + meta.json) is committed to git and
baked into the ai image like every other champion (forecast/ETA/recsys
precedent). Key-free CI gates in evals/suites/test_sentiment_serving_assets.py
re-verify parity on every merge.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch

from dosadash_ml.finetune.benchmark import load_champion
from dosadash_ml.finetune.dataset import LABELS, build_examples
from dosadash_ml.finetune.metrics import label_set_metrics
from dosadash_ml.finetune.predict import (
    CONFIDENCE_MARGIN,
    ONNX_META_FILE,
    ONNX_MODEL_FILE,
    ONNX_TOKENIZER_FILE,
    is_confident,
    load_sentiment_champion,
    predict_probs,
    predict_sentiment,
)
from dosadash_ml.finetune.train import encode, probs_to_label_sets
from dosadash_ml.finetune.train import predict_probs as torch_predict_probs

ARTIFACTS = Path(__file__).resolve().parents[3] / "artifacts" / "sentiment"


class _LogitsOnly(torch.nn.Module):
    """ONNX-friendly wrapper: (input_ids, attention_mask) → logits tensor."""

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        return self.model(input_ids=input_ids, attention_mask=attention_mask).logits


def export_onnx(model: torch.nn.Module, out_path: Path, max_len: int) -> None:
    wrapper = _LogitsOnly(model).eval()
    dummy = (
        torch.zeros((2, max_len), dtype=torch.long),
        torch.ones((2, max_len), dtype=torch.long),
    )
    torch.onnx.export(
        wrapper,
        dummy,
        str(out_path),
        input_names=["input_ids", "attention_mask"],
        output_names=["logits"],
        dynamic_axes={
            "input_ids": {0: "batch"},
            "attention_mask": {0: "batch"},
            "logits": {0: "batch"},
        },
        opset_version=17,
        dynamo=False,  # classic exporter: static graph, no torch at serve time
    )


def quantize_int8(fp32_path: Path, int8_path: Path) -> None:
    # QUInt8 chosen EMPIRICALLY over QInt8: on the same export, signed
    # weights cost 0.8 macro-F1 pts (0.9875) vs 0.2 for unsigned (0.9937)
    # against the fp32 reference 0.9956 — same lesson as recsys' log1p
    # confidences: measure, don't assume.
    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(str(fp32_path), str(int8_path), weight_type=QuantType.QUInt8)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export INT8 ONNX sentiment champion")
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS)
    args = parser.parse_args()

    champ_dir = args.artifacts_dir / "champion"
    onnx_dir = args.artifacts_dir / "onnx"
    onnx_dir.mkdir(parents=True, exist_ok=True)

    model, tokenizer, meta = load_champion(champ_dir)
    max_len = int(meta["max_len"])
    merged = model.merge_and_unload()  # LoRA deltas folded into the base weights
    merged.eval()

    # ---- torch fp32 reference on the shared held-out test split
    test = [e for e in build_examples() if e.split == "test"]
    loader = torch.utils.data.DataLoader(encode(test, tokenizer, max_len), batch_size=64)
    fp32_probs = torch_predict_probs(merged, loader)
    fp32_metrics = label_set_metrics(probs_to_label_sets(fp32_probs, meta["threshold"]), test)
    print(f"fp32 (merged) test: {json.dumps(fp32_metrics)}")

    # ---- ONNX export + INT8 quantization
    fp32_path = onnx_dir / "model.fp32.onnx"
    int8_path = onnx_dir / ONNX_MODEL_FILE
    export_onnx(merged, fp32_path, max_len)
    quantize_int8(fp32_path, int8_path)
    fp32_path.unlink()  # only the INT8 artifact is committed/served

    # ---- tokenizer + meta for the torch-free serving path
    tokenizer.backend_tokenizer.save(str(onnx_dir / ONNX_TOKENIZER_FILE))
    base_meta = {
        "model_version": f"{meta['model_version']}-int8",
        "base_model": meta["base_model"],
        "labels": list(LABELS),
        "threshold": meta["threshold"],
        "max_len": max_len,
        "pad_id": int(tokenizer.pad_token_id),
        "pad_token": tokenizer.pad_token,
        "confidence_margin": CONFIDENCE_MARGIN,
    }
    (onnx_dir / ONNX_META_FILE).write_text(json.dumps(base_meta, indent=2) + "\n")

    # ---- parity through the EXACT serving code path
    champion = load_sentiment_champion(args.artifacts_dir.parent)
    texts = [e.text for e in test]
    started = time.time()
    int8_probs = predict_probs(champion, texts)
    int8_seconds = time.time() - started
    int8_preds = [
        {champion.labels[j] for j in np.flatnonzero(row >= champion.threshold)}
        for row in int8_probs
    ]
    int8_metrics = label_set_metrics(int8_preds, test)

    served = predict_sentiment(champion, texts)
    confident_idx = [i for i, p in enumerate(served) if p.confident]
    confident_metrics = label_set_metrics(
        [int8_preds[i] for i in confident_idx], [test[i] for i in confident_idx]
    )
    coverage = len(confident_idx) / len(test)

    meta_out = {
        **base_meta,
        "exported_at": datetime.now(UTC).isoformat(),
        "int8_bytes": int8_path.stat().st_size,
        "test_n": len(test),
        "fp32_macro_f1": fp32_metrics["macro_f1"],
        "int8_macro_f1": int8_metrics["macro_f1"],
        "int8_exact_match": int8_metrics["exact_match"],
        "quantization_macro_f1_delta": round(
            int8_metrics["macro_f1"] - fp32_metrics["macro_f1"], 6
        ),
        "cpu_ms_per_review_int8": round(int8_seconds / len(test) * 1000, 2),
        "confident_coverage": round(coverage, 4),
        "confident_macro_f1": confident_metrics["macro_f1"],
        "confident_exact_match": confident_metrics["exact_match"],
        "note": (
            "parity measured through dosadash_ml.finetune.predict — the exact "
            "serving path; unconfident reviews escalate to the LLM at serve time"
        ),
    }
    (onnx_dir / ONNX_META_FILE).write_text(json.dumps(meta_out, indent=2) + "\n")
    verify = is_confident(np.array([0.99] * len(LABELS)), champion.threshold)
    assert verify, "is_confident sanity check failed"
    print(json.dumps({k: v for k, v in meta_out.items() if k != "labels"}, indent=2))
    print(f"wrote {int8_path} ({int8_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
