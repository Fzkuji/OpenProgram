"""The paper fixture for the reproduce tests."""

import pytest

from .repo import make


@pytest.fixture
def paper(tmp_path):
    return make(tmp_path)
