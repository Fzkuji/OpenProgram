from pathlib import Path

import pytest


WEB = Path(__file__).parent


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.is_relative_to(WEB):
            item.add_marker(pytest.mark.browser)
