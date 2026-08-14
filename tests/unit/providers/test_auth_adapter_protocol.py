"""Conformance tests for ProviderAuthContract.

For each shipped provider auth_adapter module, assert it matches the
Protocol: non-empty PROVIDER_ID and at least one import_from_<source>
helper when the provider's only credential source is external (CLI
subscription flows).
"""
from __future__ import annotations

import importlib

import pytest

from openprogram.auth.provider_contract import (
    AdapterConformanceError,
    validate_provider_auth_module,
)


# Known adapters that ship with this codebase. Three of them rely on
# reading an external vendor-CLI credential store, so their external
# import helper is mandatory. Anthropic exposes PKCE-based login and
# env-key routes, so the file-import helper is optional there.
_ADAPTER_MODULES = [
    ("openprogram.providers.openai_codex.auth_adapter", True),
    ("openprogram.providers.google_gemini_cli.auth_adapter", True),
    ("openprogram.providers.github_copilot.auth_adapter", True),
    ("openprogram.providers.anthropic.auth_adapter", False),
]


@pytest.mark.parametrize("module_path,require_import", _ADAPTER_MODULES)
def test_shipped_adapter_conforms(module_path: str, require_import: bool) -> None:
    mod = importlib.import_module(module_path)
    validate_provider_auth_module(mod, require_import=require_import)
    assert isinstance(mod.PROVIDER_ID, str) and mod.PROVIDER_ID


def test_validate_rejects_missing_provider_id():
    class Fake:
        __name__ = "fake.adapter"
        # no PROVIDER_ID
    with pytest.raises(AdapterConformanceError, match="PROVIDER_ID"):
        validate_provider_auth_module(Fake())


def test_validate_rejects_missing_import_when_required():
    class Fake:
        __name__ = "fake.adapter"
        PROVIDER_ID = "fake"
    with pytest.raises(AdapterConformanceError, match="import_from"):
        validate_provider_auth_module(Fake(), require_import=True)


def test_validate_accepts_legacy_import_from_name():
    class Fake:
        __name__ = "fake.adapter"
        PROVIDER_ID = "fake"
        @staticmethod
        def import_from_my_vendor(*, account_id: str = "default"):
            return None
    # Doesn't raise — ``import_from_<source>`` counts.
    validate_provider_auth_module(Fake(), require_import=True)


def test_validate_accepts_canonical_import_from_external():
    class Fake:
        __name__ = "fake.adapter"
        PROVIDER_ID = "fake"
        @staticmethod
        def import_from_external(*, account_id: str = "default"):
            return None
    validate_provider_auth_module(Fake(), require_import=True)
