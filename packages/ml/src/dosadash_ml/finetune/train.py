"""Train the LoRA aspect-sentiment model + manage its MLflow registry entry.

    uv run --group finetune python -m dosadash_ml.finetune.train --synthetic \
        [--epochs 3] [--batch-size 32] [--max-len 96] [--lr 2e-4] \
        [--tracking-uri sqlite:///packages/ml/mlflow.db] \
        [--export-dir packages/ml/artifacts]

Base model: distilbert-base-uncased (downloaded from the HF hub at train
time) with a LoRA adapter (q/v projections) + a trained classification head,
as multi-label classification over the 16 (aspect × polarity) labels.
Training runs on CPU BY DESIGN — the whole Phase 8 story is that a tiny
tuned model matches zero-shot LLM accuracy at CPU-serving cost.

Evaluation (val during training, test only at the end): micro/macro F1 at
0.5, exact-match on the label set, and overall-sentiment accuracy via the
same deterministic rollup the serving guardrail uses. Promotion: `champion`
alias moves iff macro-F1 improves — same contract as forecasting/ETA/recsys.

Export: ONLY the LoRA adapter + head (a few MB — committed to git) plus
meta.json. The frozen DistilBERT base is pulled from the hub by name; the
INT8 CPU-serving artifact is built from this champion in the serving slice.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import mlflow
import numpy as np
import torch
from mlflow.tracking import MlflowClient
from peft import LoraConfig, TaskType, get_peft_model
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from dosadash_ml.finetune.dataset import LABELS, Example, build_examples, multi_hot
from dosadash_ml.finetune.metrics import label_set_metrics

MODEL_NAME = "dosadash-sentiment"
CHAMPION = "champion"
BASE_MODEL = "distilbert-base-uncased"
PROMOTE_METRIC = "macro_f1"
THRESHOLD = 0.5


def encode(
    examples: list[Example], tokenizer: AutoTokenizer, max_len: int
) -> torch.utils.data.TensorDataset:
    enc = tokenizer(
        [e.text for e in examples],
        truncation=True,
        padding="max_length",
        max_length=max_len,
        return_tensors="pt",
    )
    labels = torch.tensor([multi_hot(e.labels) for e in examples])
    return torch.utils.data.TensorDataset(enc["input_ids"], enc["attention_mask"], labels)


def build_model(*, r: int = 8, alpha: int = 16, dropout: float = 0.05) -> torch.nn.Module:
    base = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL, num_labels=len(LABELS), problem_type="multi_label_classification"
    )
    config = LoraConfig(
        task_type=TaskType.SEQ_CLS,
        r=r,
        lora_alpha=alpha,
        lora_dropout=dropout,
        target_modules=["q_lin", "v_lin"],
        modules_to_save=["classifier", "pre_classifier"],
    )
    return get_peft_model(base, config)


@torch.no_grad()
def predict_probs(model: torch.nn.Module, loader: DataLoader) -> np.ndarray:
    model.eval()
    probs: list[np.ndarray] = []
    for input_ids, attention_mask, _ in loader:
        logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
        probs.append(torch.sigmoid(logits).cpu().numpy())
    return np.concatenate(probs)


def probs_to_label_sets(probs: np.ndarray, threshold: float = THRESHOLD) -> list[set[str]]:
    return [{LABELS[j] for j in np.flatnonzero(row >= threshold)} for row in probs]


def metrics_from_probs(probs: np.ndarray, examples: list[Example]) -> dict[str, float]:
    """Thresholded probabilities → the SHARED label-set metrics (same math
    scores the zero-shot side of the benchmark — see metrics.py)."""
    return label_set_metrics(probs_to_label_sets(probs), examples)


def train(
    model: torch.nn.Module,
    train_loader: DataLoader,
    val_loader: DataLoader,
    val_examples: list[Example],
    *,
    epochs: int,
    lr: float,
) -> None:
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr)
    for epoch in range(1, epochs + 1):
        model.train()
        total, steps = 0.0, 0
        for input_ids, attention_mask, labels in train_loader:
            optimizer.zero_grad()
            out = model(input_ids=input_ids, attention_mask=attention_mask, labels=labels)
            out.loss.backward()
            optimizer.step()
            total += float(out.loss.detach())
            steps += 1
        val = metrics_from_probs(predict_probs(model, val_loader), val_examples)
        print(
            f"epoch {epoch}/{epochs}: loss {total / steps:.4f} · "
            f"val macro_f1 {val['macro_f1']:.4f} · val exact {val['exact_match']:.4f}"
        )


def _current_champion_metric(client: MlflowClient) -> float | None:
    try:
        version = client.get_model_version_by_alias(MODEL_NAME, CHAMPION)
    except Exception:
        return None
    tag = version.tags.get(PROMOTE_METRIC)
    return float(tag) if tag is not None else None


def register_and_maybe_promote(
    metrics: dict[str, float], params: dict[str, object]
) -> tuple[str, bool]:
    client = MlflowClient()
    try:
        client.create_registered_model(MODEL_NAME)
    except Exception:
        pass
    with mlflow.start_run(run_name=f"sentiment-{datetime.now(UTC):%Y%m%d-%H%M}") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        version = client.create_model_version(
            name=MODEL_NAME, source=run.info.artifact_uri, run_id=run.info.run_id
        ).version
    client.set_model_version_tag(
        MODEL_NAME, version, PROMOTE_METRIC, f"{metrics[PROMOTE_METRIC]:.6f}"
    )
    incumbent = _current_champion_metric(client)
    promote = incumbent is None or metrics[PROMOTE_METRIC] >= incumbent
    if promote:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    return str(version), promote


def export_champion(
    model: torch.nn.Module,
    metrics: dict[str, float],
    version: str,
    params: dict[str, object],
    export_dir: Path,
) -> Path:
    champ_dir = export_dir / "sentiment" / "champion"
    champ_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(champ_dir / "adapter")  # LoRA weights + saved head only
    meta = {
        "model_version": f"{MODEL_NAME}/v{version}",
        "base_model": BASE_MODEL,
        "labels": list(LABELS),
        "threshold": THRESHOLD,
        "trained_at": datetime.now(UTC).isoformat(),
        **params,
        **{k: v for k, v in metrics.items()},
    }
    (champ_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return champ_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the LoRA aspect-sentiment model")
    parser.add_argument("--synthetic", action="store_true", required=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-len", type=int, default=96)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--tracking-uri", default="sqlite:///packages/ml/mlflow.db")
    parser.add_argument("--export-dir", type=Path, default=Path("packages/ml/artifacts"))
    args = parser.parse_args()

    torch.manual_seed(args.seed)
    mlflow.set_tracking_uri(args.tracking_uri)
    mlflow.set_experiment("dosadash-sentiment")

    examples = build_examples(seed=args.seed)
    splits = {s: [e for e in examples if e.split == s] for s in ("train", "val", "test")}
    print(
        f"dataset: {len(splits['train'])} train / {len(splits['val'])} val / "
        f"{len(splits['test'])} test (labels={len(LABELS)})"
    )

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    loaders = {
        name: DataLoader(
            encode(split, tokenizer, args.max_len),
            batch_size=args.batch_size,
            shuffle=(name == "train"),
        )
        for name, split in splits.items()
    }

    model = build_model()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"trainable params: {trainable:,} / {total:,} ({100 * trainable / total:.2f}%)")

    started = time.time()
    train(
        model,
        loaders["train"],
        loaders["val"],
        splits["val"],
        epochs=args.epochs,
        lr=args.lr,
    )
    train_seconds = round(time.time() - started, 1)

    test_metrics = metrics_from_probs(predict_probs(model, loaders["test"]), splits["test"])
    test_metrics["train_seconds"] = train_seconds
    print(f"test: {json.dumps(test_metrics, indent=2)}")

    params: dict[str, object] = {
        "base_model": BASE_MODEL,
        "epochs": args.epochs,
        "batch_size": args.batch_size,
        "max_len": args.max_len,
        "lr": args.lr,
        "seed": args.seed,
        "lora_r": 8,
        "lora_alpha": 16,
        "trainable_params": trainable,
    }
    version, promoted = register_and_maybe_promote(test_metrics, params)
    print(f"registered {MODEL_NAME} v{version} (champion={'yes' if promoted else 'no'})")
    if promoted:
        path = export_champion(model, test_metrics, version, params, args.export_dir)
        print(f"exported champion → {path}")


if __name__ == "__main__":
    main()
