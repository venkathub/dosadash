"""Benchmark the LoRA champion vs zero-shot gpt-4o-mini (Phase 8).

    uv run --group finetune python -m dosadash_ml.finetune.benchmark

Loads the committed champion adapter, predicts the SAME test split the live
zero-shot run scored (evals/suites/review_zero_shot_eval.py →
artifacts/sentiment/zero_shot.json), scores both sides with the SHARED
metrics, measures CPU latency, and writes
`packages/ml/artifacts/sentiment/benchmark.json` — the accuracy-vs-₹/1k
table the README quotes.

Honesty notes recorded in the artifact: labels are synthetic-planted (the
benchmark validates label-recovery on this distribution, not human
agreement), and zero-shot cost is estimated from token counts at published
prices.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import torch
from peft import PeftModel
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from dosadash_ml.finetune.dataset import LABELS, build_examples
from dosadash_ml.finetune.metrics import label_set_metrics
from dosadash_ml.finetune.train import encode, predict_probs, probs_to_label_sets

ARTIFACTS = Path(__file__).resolve().parents[3] / "artifacts" / "sentiment"


def load_champion(champ_dir: Path) -> tuple[torch.nn.Module, AutoTokenizer, dict]:
    meta = json.loads((champ_dir / "meta.json").read_text())
    base = AutoModelForSequenceClassification.from_pretrained(
        meta["base_model"],
        num_labels=len(meta["labels"]),
        problem_type="multi_label_classification",
    )
    model = PeftModel.from_pretrained(base, champ_dir / "adapter")
    model.eval()
    return model, AutoTokenizer.from_pretrained(meta["base_model"]), meta


def main() -> None:
    parser = argparse.ArgumentParser(description="LoRA vs zero-shot benchmark")
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS)
    parser.add_argument("--max-len", type=int, default=96)
    args = parser.parse_args()

    champ_dir = args.artifacts_dir / "champion"
    zero_shot_path = args.artifacts_dir / "zero_shot.json"
    model, tokenizer, meta = load_champion(champ_dir)
    zero_shot = json.loads(zero_shot_path.read_text())

    test = [e for e in build_examples() if e.split == "test"]
    subset = test[: zero_shot["sample"]]
    assert meta["labels"] == list(LABELS), "label space drifted since training"

    loader = DataLoader(encode(test, tokenizer, args.max_len), batch_size=64)
    started = time.time()
    probs = predict_probs(model, loader)
    lora_seconds = time.time() - started
    preds = probs_to_label_sets(probs, meta["threshold"])

    lora_full = label_set_metrics(preds, test)
    lora_subset = label_set_metrics(preds[: len(subset)], subset)
    zs_preds = [set(p) for p in zero_shot["predictions"]]
    zs_metrics = label_set_metrics(zs_preds, subset)  # recomputed, same math

    ms_per_review = lora_seconds / len(test) * 1000
    zs_cost = zero_shot["cost_inr_per_1k_reviews"]
    artifact = {
        "model_version": meta["model_version"],
        "subset_n": len(subset),
        "test_n": len(test),
        "lora": {
            "full_test": lora_full,
            "zero_shot_subset": lora_subset,
            "cpu_ms_per_review": round(ms_per_review, 2),
            "cost_inr_per_1k_reviews": 0.0,
            "cost_note": "self-hosted CPU inference on the existing VPS — zero marginal cost",
        },
        "zero_shot": {
            "models_used": zero_shot["models_used"],
            "prompt_version": zero_shot["prompt_version"],
            "zero_shot_subset": zs_metrics,
            "cost_inr_per_1k_reviews": zs_cost,
        },
        "macro_f1_gap_on_subset": round(lora_subset["macro_f1"] - zs_metrics["macro_f1"], 6),
        "ran_at": datetime.now(UTC).isoformat(),
        "honesty": (
            "planted synthetic labels: measures label recovery on this distribution, "
            "not human agreement; zero-shot cost estimated from token counts"
        ),
    }
    out = args.artifacts_dir / "benchmark.json"
    out.write_text(json.dumps(artifact, indent=2) + "\n")
    print(json.dumps(artifact, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
