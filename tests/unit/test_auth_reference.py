"""Tests for AuthReference — pointer to externally-owned credential state."""
from __future__ import annotations

import pytest

from openprogram.auth.types import AuthReference


def test_external_file_reference():
    ref = AuthReference(
        kind="external_file",
        store_path="~/.claude/.credentials.json",
    )
    assert ref.kind == "external_file"
    assert ref.store_path == "~/.claude/.credentials.json"


def test_external_file_requires_store_path():
    with pytest.raises(ValueError, match="store_path"):
        AuthReference(kind="external_file")


def test_credential_ref_shape():
    ref = AuthReference(
        kind="credential_ref",
        provider_id="chatgpt-subscription",
        profile_id="work",
    )
    assert ref.provider_id == "chatgpt-subscription"
    assert ref.profile_id == "work"


def test_credential_ref_requires_both_ids():
    with pytest.raises(ValueError, match="provider_id"):
        AuthReference(kind="credential_ref", profile_id="default")
    with pytest.raises(ValueError, match="provider_id"):
        AuthReference(kind="credential_ref", provider_id="anthropic")


def test_frozen():
    ref = AuthReference(kind="external_file", store_path="/tmp/x")
    with pytest.raises(Exception):
        ref.store_path = "/tmp/y"  # type: ignore[misc]


def test_metadata_round_trip():
    ref = AuthReference(
        kind="external_file",
        store_path="/tmp/x",
        metadata={"home_env": "CLAUDE_HOME"},
    )
    assert ref.metadata["home_env"] == "CLAUDE_HOME"
