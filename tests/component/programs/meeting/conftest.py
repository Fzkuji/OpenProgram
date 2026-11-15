"""Meeting tests never download the speaker-diarization models."""
from __future__ import annotations

import pytest

from openprogram.programs.applications.meeting import diarize


def _no_models(chunks):
    raise diarize.Unavailable("no_model", "no speaker models in tests")


@pytest.fixture(autouse=True)
def no_diarization_models(monkeypatch):
    monkeypatch.setattr(diarize, "run", _no_models)
