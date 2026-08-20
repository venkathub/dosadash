"""Key-free CI gates for the Phase 8 LoRA-vs-zero-shot benchmark artifacts.

The committed artifacts under packages/ml/artifacts/sentiment/ ARE the
resume claim ("LoRA matches zero-shot accuracy at API-free serving cost") —
these gates keep the claim honest and pinned:

- the champion must actually be good (macro-F1 floor)
- the LoRA side must stay within a bounded gap of zero-shot ON THE SAME
  holdout subset, scored by the same shared metrics
- the economics must hold (zero-shot costs real money, LoRA serves in CPU
  milliseconds), and the committed adapter must stay adapter-SIZED — an
  accidental full-model commit fails CI before it bloats the repo
- label-space drift between the registry, the dataset and the trained
  model fails loudly

No torch, no keys: gates read committed JSON only.
"""

import json
from pathlib import Path

from dosadash_ml.finetune.dataset import LABELS
from dosadash_shared import REVIEW_ASPECTS, REVIEW_SENTIMENT_PROMPT_VERSION

SENTIMENT = Path(__file__).resolve().parents[2] / "packages" / "ml" / "artifacts" / "sentiment"
CHAMPION = SENTIMENT / "champion"

MACRO_F1_FLOOR = 0.95
MAX_MACRO_F1_GAP = 0.03  # LoRA may trail zero-shot by at most 3 points on the shared subset
MAX_CPU_MS_PER_REVIEW = 500.0  # must be CPU-serveable on the VPS
MAX_ADAPTER_BYTES = 30 * 1024 * 1024


def _load(name: str) -> dict:
    return json.loads((SENTIMENT / name).read_text())


def test_artifacts_exist_and_parse():
    meta = json.loads((CHAMPION / "meta.json").read_text())
    assert meta["model_version"].startswith("dosadash-sentiment/")
    assert (CHAMPION / "adapter" / "adapter_config.json").exists()
    assert _load("zero_shot.json")["sample"] >= 200
    assert _load("benchmark.json")["subset_n"] >= 200


def test_champion_label_space_matches_registry():
    meta = json.loads((CHAMPION / "meta.json").read_text())
    assert meta["labels"] == list(LABELS)
    assert len(LABELS) == 2 * len(REVIEW_ASPECTS)


def test_champion_macro_f1_floor():
    meta = json.loads((CHAMPION / "meta.json").read_text())
    assert meta["macro_f1"] >= MACRO_F1_FLOOR, meta["macro_f1"]


def test_lora_within_gap_of_zero_shot_on_shared_subset():
    bench = _load("benchmark.json")
    lora = bench["lora"]["zero_shot_subset"]["macro_f1"]
    zs = bench["zero_shot"]["zero_shot_subset"]["macro_f1"]
    assert lora >= zs - MAX_MACRO_F1_GAP, (lora, zs)
    # both sides scored the identical subset with the identical metric
    assert bench["lora"]["zero_shot_subset"]["examples"] == bench["subset_n"]
    assert bench["zero_shot"]["zero_shot_subset"]["examples"] == bench["subset_n"]


def test_economics_hold():
    bench = _load("benchmark.json")
    assert bench["zero_shot"]["cost_inr_per_1k_reviews"] > 0
    assert bench["lora"]["cost_inr_per_1k_reviews"] == 0.0
    assert bench["lora"]["cpu_ms_per_review"] <= MAX_CPU_MS_PER_REVIEW
    # cost basis must be recorded, not vibes
    zs = _load("zero_shot.json")
    assert zs["est_input_tokens"] > 0 and zs["est_output_tokens"] > 0
    assert "methodology" in zs
    assert "honesty" in bench


def test_zero_shot_side_used_the_production_prompt():
    zs = _load("zero_shot.json")
    assert zs["prompt_version"] == REVIEW_SENTIMENT_PROMPT_VERSION
    assert zs["models_used"], "no model recorded"


def test_committed_adapter_stays_adapter_sized():
    total = sum(f.stat().st_size for f in (CHAMPION / "adapter").rglob("*") if f.is_file())
    assert total <= MAX_ADAPTER_BYTES, f"{total} bytes — did a full model get committed?"


def test_benchmark_predictions_align_with_sample():
    zs = _load("zero_shot.json")
    assert len(zs["predictions"]) == zs["sample"]
    valid = set(LABELS)
    for pred in zs["predictions"]:
        assert set(pred) <= valid  # guardrail output can never leave the registry
