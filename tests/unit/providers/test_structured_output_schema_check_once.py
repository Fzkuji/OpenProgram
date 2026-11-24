"""A schema the metaschema passed is not checked again; a bad one always is."""
from __future__ import annotations

import pytest

from openprogram.models.providers import structured_output as so

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def test_one_schema_is_checked_against_the_metaschema_once(monkeypatch):
    monkeypatch.setattr(so, "_CHECKED_SCHEMAS", {})
    checked = []
    validator_for = so.validators.validator_for

    def counted(schema, *args, **kwargs):
        if schema.get("type") == "object":  # ours, not the metaschema's own lookups
            checked.append(1)
        return validator_for(schema, *args, **kwargs)

    monkeypatch.setattr(so.validators, "validator_for", counted)

    first = so.normalize_response_format({"type": "json_schema", "schema": SCHEMA})
    once = len(checked)
    assert once >= 1
    for _ in range(20):
        again = so.normalize_response_format({"type": "json_schema", "schema": dict(SCHEMA)})
    assert again.schema == first.schema and again.schema is not first.schema
    assert len(checked) == once

    so.normalize_response_format({"type": "object", "properties": {}})
    assert len(checked) == 2 * once, "another schema is checked"


def test_an_invalid_schema_is_refused_every_time(monkeypatch):
    monkeypatch.setattr(so, "_CHECKED_SCHEMAS", {})
    for _ in range(2):
        with pytest.raises(so.StructuredOutputSchemaError):
            so.normalize_response_format({"type": "object", "properties": {"x": {"type": "no-such-type"}}})
    assert so._CHECKED_SCHEMAS == {}


def test_the_kept_passes_are_bounded(monkeypatch):
    monkeypatch.setattr(so, "_CHECKED_SCHEMAS", {})
    monkeypatch.setattr(so, "_CHECKED_SCHEMAS_KEPT", 3)
    for n in range(6):
        so.normalize_response_format({"type": "object", "title": f"schema {n}"})
    assert len(so._CHECKED_SCHEMAS) == 3
