"""Validation-only promotion policy; legacy test-based scores are not comparable."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlflow.tracking import MlflowClient

MODEL_NAME = "dosadash-sentiment"
CHAMPION = "champion"
PROMOTE_METRIC = "macro_f1"


def may_promote(
    candidate: float,
    validation_sha256: str,
    incumbent_tags: dict[str, str] | None,
) -> bool:
    if not math.isfinite(candidate) or not 0 <= candidate <= 1 or not validation_sha256:
        raise ValueError("finite validation F1 and a validation dataset fingerprint are required")
    if incumbent_tags is None:
        return True  # no champion exists
    if (
        incumbent_tags.get("selection_split") != "val"
        or incumbent_tags.get("validation_sha256") != validation_sha256
    ):
        return False  # rescore the incumbent on the same validation set first
    try:
        incumbent = float(incumbent_tags["macro_f1"])
    except (KeyError, ValueError):
        return False
    return math.isfinite(incumbent) and 0 <= incumbent <= 1 and candidate > incumbent


def _current_champion_tags(client: MlflowClient) -> dict[str, str] | None:
    # MLflow reports a missing alias as INVALID_PARAMETER_VALUE, the same code
    # used for other invalid requests. Inspect explicit alias metadata instead
    # of treating exceptions as proof that the registry has no champion.
    registered = client.get_registered_model(MODEL_NAME)
    if CHAMPION not in registered.aliases:
        return None
    return dict(client.get_model_version_by_alias(MODEL_NAME, CHAMPION).tags)


def register_and_maybe_promote(
    metrics: dict[str, float], params: dict[str, object]
) -> tuple[str, bool]:
    import mlflow
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    if params.get("selection_split") != "val" or not params.get("validation_sha256"):
        raise ValueError("promotion requires validation provenance")
    client = MlflowClient()
    try:
        client.create_registered_model(MODEL_NAME)
    except MlflowException as exc:
        if exc.error_code != "RESOURCE_ALREADY_EXISTS":
            raise
    with mlflow.start_run(run_name=f"sentiment-{datetime.now(UTC):%Y%m%d-%H%M}") as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        version = client.create_model_version(
            name=MODEL_NAME, source=run.info.artifact_uri, run_id=run.info.run_id
        ).version
    client.set_model_version_tag(
        MODEL_NAME, version, PROMOTE_METRIC, f"{metrics[PROMOTE_METRIC]:.6f}"
    )
    validation_sha256 = str(params["validation_sha256"])
    client.set_model_version_tag(MODEL_NAME, version, "selection_split", "val")
    client.set_model_version_tag(MODEL_NAME, version, "validation_sha256", validation_sha256)
    incumbent = _current_champion_tags(client)
    promote = may_promote(metrics[PROMOTE_METRIC], validation_sha256, incumbent)
    if promote:
        client.set_registered_model_alias(MODEL_NAME, CHAMPION, version)
    return str(version), promote
