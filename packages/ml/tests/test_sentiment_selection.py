"""Test-set scores and incomparable incumbents must never select a champion."""

import pytest

from dosadash_ml.finetune.selection import may_promote


def test_selection_requires_strict_validation_improvement_on_same_dataset():
    incumbent = {"selection_split": "val", "validation_sha256": "abc", "macro_f1": "0.8"}
    assert may_promote(0.81, "abc", incumbent)
    assert not may_promote(0.8, "abc", incumbent)
    assert not may_promote(0.79, "abc", incumbent)
    assert not may_promote(1.0, "different", incumbent)


def test_legacy_test_score_does_not_authorize_promotion():
    assert not may_promote(1.0, "abc", {"macro_f1": "0.1"})
    assert not may_promote(1.0, "abc", {"selection_split": "test", "macro_f1": "0.1"})
    assert may_promote(0.8, "abc", None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_metric_cannot_promote(value):
    with pytest.raises(ValueError):
        may_promote(value, "abc", None)


@pytest.mark.parametrize("value", ["nan", "oops", "-1", "2"])
def test_invalid_incumbent_requires_rescoring(value):
    assert not may_promote(
        0.9,
        "abc",
        {
            "selection_split": "val",
            "validation_sha256": "abc",
            "macro_f1": value,
        },
    )


def test_registry_unavailable_cannot_be_treated_as_empty():
    from mlflow.exceptions import MlflowException

    from dosadash_ml.finetune.selection import _current_champion_tags

    class BrokenRegistry:
        def get_model_version_by_alias(self, *args):
            raise MlflowException("network unavailable", error_code="INTERNAL_ERROR")

    with pytest.raises(MlflowException):
        _current_champion_tags(BrokenRegistry())


def test_registry_selection_records_validation_and_retains_legacy_champion(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    import mlflow
    import mlflow.tracking

    from dosadash_ml.finetune.selection import register_and_maybe_promote

    tags = {}
    aliases = []

    class Registry:
        def create_registered_model(self, *args):
            pass

        def create_model_version(self, **kwargs):
            return SimpleNamespace(version="new")

        def set_model_version_tag(self, name, version, key, value):
            tags[key] = value

        def get_model_version_by_alias(self, *args):
            return SimpleNamespace(tags={"macro_f1": "0.1"})  # historical TEST score

        def set_registered_model_alias(self, *args):
            aliases.append(args)

    @contextmanager
    def tracking_run(**kwargs):
        yield SimpleNamespace(info=SimpleNamespace(artifact_uri="test", run_id="test"))

    monkeypatch.setattr(mlflow.tracking, "MlflowClient", Registry)
    monkeypatch.setattr(mlflow, "start_run", tracking_run)
    monkeypatch.setattr(mlflow, "log_params", lambda *args: None)
    monkeypatch.setattr(mlflow, "log_metrics", lambda *args: None)
    assert register_and_maybe_promote(
        {"macro_f1": 0.9},
        {
            "selection_split": "val",
            "validation_sha256": "unit-test",
        },
    ) == ("new", False)
    assert not aliases
    assert tags == {
        "macro_f1": "0.900000",
        "selection_split": "val",
        "validation_sha256": "unit-test",
    }
    with pytest.raises(ValueError):
        register_and_maybe_promote({"macro_f1": 1}, {"selection_split": "test"})
