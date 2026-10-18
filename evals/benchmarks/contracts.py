"""Explicit provenance and independent annotation, separate from regression fixtures."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dosadash_ml.finetune.dataset import LABELS


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Annotation(Contract):
    method: Literal["human"]
    reviewer: str = Field(min_length=1)
    labels: list[str] = Field(min_length=1)

    @field_validator("labels")
    @classmethod
    def valid_labels(cls, labels):
        if len(set(labels)) != len(labels) or set(labels) - set(LABELS):
            raise ValueError("labels must be unique registered aspect/polarity values")
        return sorted(labels)


class Adjudication(Annotation):
    reason: str = Field(min_length=10)


class ReviewRecord(Contract):
    id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    split: Literal["val", "test"]
    text: str = Field(min_length=1, max_length=2000)
    rating: int = Field(ge=1, le=5)
    language: Literal["en", "hinglish", "tanglish", "ta", "hi", "other"]
    categories: list[str] = Field(min_length=1)
    origin: Literal["permissioned_real", "human_authored", "synthetic"]
    source_ref: str = Field(min_length=1)
    permission_ref: str = Field(min_length=1)
    author: str = Field(min_length=1)
    pii_reviewed: bool = False
    independent_of_training: bool = False
    provider_use_allowed: bool = False
    annotations: list[Annotation] = Field(default_factory=list)
    adjudication: Adjudication | None = None

    @field_validator("categories")
    @classmethod
    def valid_categories(cls, values):
        if any(not value.strip() for value in values) or len(set(values)) != len(values):
            raise ValueError("categories must be nonempty and unique")
        return sorted(values)

    def resolved_labels(self):
        if len(self.annotations) < 2:
            raise ValueError("two independent annotations are required")
        reviewers = [a.reviewer for a in self.annotations]
        if len(set(reviewers)) != len(reviewers) or self.author in reviewers:
            raise ValueError("annotators must be distinct and independent of the author")
        if self.adjudication:
            if self.adjudication.reviewer in {*reviewers, self.author}:
                raise ValueError("adjudicator must be independent of author and annotators")
            return self.adjudication.labels
        if any(a.labels != self.annotations[0].labels for a in self.annotations[1:]):
            raise ValueError("annotation disagreement requires adjudication")
        return self.annotations[0].labels


class Protocol(Contract):
    backend: Literal["onnx", "llm", "compare"] = "onnx"
    min_groups_per_split: int = Field(default=30, ge=1)
    bootstrap_samples: int = Field(default=1000, ge=100, le=10000)
    seed: int = 42
    timeout_seconds: int = Field(default=120, ge=1, le=300)
    max_calls: int = Field(default=1000, ge=1, le=5000)
    max_tokens: int = Field(default=2000000, ge=1)
    max_cost_usd: float = Field(default=1.0, gt=0, le=5, allow_inf_nan=False)


class FrozenDataset(Contract):
    schema_version: Literal[1] = 1
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,79}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,39}$")
    records_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    protocol: Protocol
    audit: dict
    frozen_at: str

    @model_validator(mode="after")
    def clean_audit(self):
        if self.audit.get("issues") or not self.audit.get("eligible"):
            raise ValueError("cannot freeze an ineligible dataset")
        return self
