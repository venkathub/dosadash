"""Fail-closed collection audit, overlap checks and frozen input fingerprints."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from dosadash_ml.datagen.reviews import TEMPLATES
from dosadash_ml.finetune.dataset import build_examples

from .contracts import FrozenDataset, ReviewRecord

ROOT = Path(__file__).resolve().parents[2]


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(re.findall(r"\w+", text))


def load_records(path):
    records = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                records.append(ReviewRecord.model_validate_json(line))
            except ValueError:
                # Validation exceptions can embed raw, unredacted input values.
                raise ValueError(f"invalid review record on line {number}") from None
    if not records:
        raise ValueError("empty benchmark dataset")
    return records


def reference_corpus():
    """Known synthetic distribution plus existing review regression fixtures.

    All known splits are excluded, not only synthetic training rows. Additional
    external training corpora must be supplied with --references.
    """
    rows = [{"id": f"synthetic:{i}", "text": e.text} for i, e in enumerate(build_examples())]
    path = ROOT / "evals/golden/review_sentiment.jsonl"
    for line in path.read_text().splitlines():
        case = json.loads(line)
        rows.extend(
            {"id": f"golden:{case['id']}:{i}", "text": r["text"]}
            for i, r in enumerate(case["reviews"])
            if r.get("text")
        )
    return rows


def load_references(path=None):
    rows = reference_corpus()
    if path is not None:
        for i, line in enumerate(path.read_text().splitlines()):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value.get("text"), str) or not value["text"].strip():
                    raise ValueError("external reference rows require nonempty text")
                rows.append({"id": f"external:{i}", "text": value["text"]})
    return rows


def template_patterns():
    patterns = []
    for languages in TEMPLATES.values():
        for templates in languages.values():
            for template in templates:
                value = normalized(template.replace("{dish}", "BENCHDISHPLACEHOLDER"))
                pattern = re.escape(value).replace("benchdishplaceholder", r"(?:\w+\s+){0,8}\w+")
                patterns.append(re.compile(r"(?<!\w)" + pattern + r"(?!\w)"))
    return patterns


class OverlapIndex:
    """Exact normalized text + token Jaccard >= .8 for >=4-token near duplicates."""

    def __init__(self, references):
        self.rows = []
        self.exact = defaultdict(list)
        self.inverted = defaultdict(set)
        for ref in references:
            value = normalized(ref["text"])
            tokens = set(value.split())
            i = len(self.rows)
            self.rows.append((ref["id"], tokens))
            self.exact[value].append(ref["id"])
            for token in tokens:
                self.inverted[token].add(i)

    def matches(self, text):
        value = normalized(text)
        if value in self.exact:
            return self.exact[value][:5]
        tokens = set(value.split())
        if len(tokens) < 4:
            return []
        counts = defaultdict(int)
        for token in tokens:
            for i in self.inverted.get(token, ()):
                counts[i] += 1
        return [
            self.rows[i][0]
            for i, common in counts.items()
            if common / len(tokens | self.rows[i][1]) >= 0.8
        ][:5]


def audit(records, references):
    issues = []
    ids = set()
    groups = defaultdict(set)
    counts = defaultdict(int)
    disagreements = 0
    reference_index = OverlapIndex(references)
    patterns = template_patterns()
    seen = []
    for record in records:
        reasons = []
        if record.id in ids:
            reasons.append("duplicate id")
        ids.add(record.id)
        groups[record.group_id].add(record.split)
        counts[record.split] += 1
        if record.origin == "synthetic":
            reasons.append("synthetic examples are regression fixtures, not independent evidence")
        if not record.pii_reviewed or not record.independent_of_training:
            reasons.append("PII review and independent-source attestation required")
        try:
            record.resolved_labels()
        except ValueError as exc:
            reasons.append(str(exc))
        disagreements += len({tuple(a.labels) for a in record.annotations}) > 1
        matches = reference_index.matches(record.text)
        if matches:
            reasons.append("overlap with known data: " + ", ".join(matches))
        if any(p.search(normalized(record.text)) for p in patterns):
            reasons.append("contains a known synthetic review template")
        for earlier in seen:
            # Repeated independent texts are not extra observations, even within a split.
            if OverlapIndex([{"id": earlier.id, "text": earlier.text}]).matches(record.text):
                reasons.append("duplicate/near-duplicate benchmark text: " + earlier.id)
        seen.append(record)
        if reasons:
            issues.append({"id": record.id, "reasons": reasons})
    for group, splits in groups.items():
        if len(splits) > 1:
            issues.append({"group_id": group, "reasons": ["source group crosses val/test"]})
    if not counts.get("val") or not counts.get("test"):
        issues.append({"reasons": ["both validation and test splits must be nonempty"]})
    return {
        "eligible": not issues,
        "issues": issues,
        "counts": dict(counts),
        "groups": len(groups),
        "annotation_disagreements": disagreements,
        "annotation_disagreement_rate": disagreements / len(records) if records else None,
        "overlap_method": "NFKC/casefold exact, token Jaccard >=0.8, known template spans",
        "limitations": "Attestations require human verification; semantic paraphrases may escape.",
    }


def candidate_fingerprint(model_dir, protocol):
    paths = list(Path(__file__).resolve().parent.glob("*.py"))
    paths += [
        ROOT / "apps/ai/src/dosadash_ai/routers/reviews.py",
        ROOT / "apps/ai/src/dosadash_ai/llm/client.py",
        ROOT / "apps/ai/src/dosadash_ai/redaction.py",
        ROOT / "packages/ml/src/dosadash_ml/finetune/dataset.py",
        ROOT / "packages/shared/src/dosadash_shared/reviews.py",
        ROOT / "uv.lock",
        ROOT / "apps/ai/src/dosadash_ai/llm/eval_telemetry.py",
        ROOT / "packages/ml/src/dosadash_ml/finetune/predict.py",
        ROOT / "packages/ml/src/dosadash_ml/finetune/metrics.py",
        ROOT / "apps/ai/prompts/review_sentiment_v1.md",
    ]
    files = {str(p.relative_to(ROOT)): file_hash(p) for p in paths}
    if protocol.backend in {"onnx", "compare"}:
        for name in ("model.int8.onnx", "tokenizer.json", "meta.json"):
            files[f"model/{name}"] = file_hash(model_dir / "sentiment/onnx" / name)
    if protocol.backend in {"llm", "compare"}:
        from dosadash_ai.config import get_settings

        settings = get_settings()
        files["model_chain"] = settings.llm_models
    return fingerprint({"files": files, "protocol": protocol.model_dump()})


def freeze(records_path, references, *, dataset_id, version, protocol, model_dir):
    records = load_records(records_path)
    result = audit(records, references)
    if not result["eligible"]:
        raise ValueError("dataset audit failed; run audit to inspect record IDs and reasons")
    for split in ("val", "test"):
        groups = {r.group_id for r in records if r.split == split}
        if len(groups) < protocol.min_groups_per_split:
            raise ValueError(
                f"{split} needs at least {protocol.min_groups_per_split} source groups"
            )
    if protocol.backend in {"llm", "compare"} and not all(r.provider_use_allowed for r in records):
        raise ValueError("provider-use permission is required for every external-model input")
    return FrozenDataset(
        dataset_id=dataset_id,
        version=version,
        records_sha256=file_hash(records_path),
        reference_sha256=fingerprint(references),
        candidate_sha256=candidate_fingerprint(model_dir, protocol),
        protocol=protocol,
        audit=result,
        frozen_at=datetime.now(UTC).isoformat(),
    )
