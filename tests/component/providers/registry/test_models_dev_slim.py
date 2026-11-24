"""The catalogue kept in memory answers as the whole one does, in less room."""
from __future__ import annotations

from openprogram.models.providers.catalog.sources import models_dev


def _catalogue():
    row = {
        "id": "m1", "name": "Model One", "description": "long text " * 200,
        "canonical_model_id": "vendor/m1", "family": "one", "reasoning": True, "tool_call": True,
        "modalities": {"input": ["text", "image"], "output": ["text"]},
        "limit": {"context": 200000, "output": 8000},
        "cost": {"input": 1.0, "output": 2.0, "tiers": [{"over": 200000, "input": 2.0}]},
        "experimental": {"modes": {"fast": {"provider": {"body": {"service_tier": "priority"}}}}},
    }
    return {
        "a": {"id": "a", "name": "A", "env": ["A_KEY"], "api": "https://a", "doc": "https://a/doc",
              "npm": "@a/sdk", "models": {"m1": dict(row)}},
        "b": {"id": "b", "name": "B", "env": ["B_KEY"], "models": {"m1": dict(row)}},
    }


def test_every_reader_gets_the_same_answer_from_the_slim_catalogue():
    whole, slim = _catalogue(), models_dev._slim(_catalogue())
    for provider in whole:
        assert (models_dev._normalise_provider(provider, slim[provider])
                == models_dev._normalise_provider(provider, whole[provider]))
        assert (models_dev._normalise(slim[provider]["models"]["m1"])
                == models_dev._normalise(whole[provider]["models"]["m1"]))


def test_unread_fields_are_dropped_and_equal_values_are_one_object():
    slim = models_dev._slim(_catalogue())
    assert "description" not in slim["a"]["models"]["m1"]
    assert slim["a"]["models"]["m1"]["limit"] is slim["b"]["models"]["m1"]["limit"]
