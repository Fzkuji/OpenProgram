from pathlib import Path

import pytest


LIVE = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.is_relative_to(LIVE):
            item.add_marker(pytest.mark.live)
