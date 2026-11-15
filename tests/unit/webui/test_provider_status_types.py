"""The web's provider status types are generated from the Python payload."""
from __future__ import annotations

from scripts.generate_provider_status_types import TARGET, render


def test_the_generated_types_match_the_payload() -> None:
    assert TARGET.read_text(encoding="utf-8") == render(), "run python -m scripts.generate_provider_status_types"
