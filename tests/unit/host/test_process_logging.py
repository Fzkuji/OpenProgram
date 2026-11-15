"""configure_process_logging: one handler, a readable format, the env override."""
from __future__ import annotations

import io
import logging

import pytest

from openprogram.host.process_logging import ENV_LEVEL, QUIET_LOGGERS, configure_process_logging


@pytest.fixture
def bare_root(monkeypatch):
    """An unconfigured root logger for the test body; the real one comes back after.

    pytest attaches its capture handlers when the test body starts, so the
    test empties the root itself by calling the fixture's value first.
    """
    root = logging.getLogger()
    monkeypatch.delenv(ENV_LEVEL, raising=False)
    saved: dict = {}

    def empty():
        saved["handlers"], saved["level"] = root.handlers[:], root.level
        saved["quiet"] = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
        root.handlers = []
        return root

    yield empty
    if saved:
        root.handlers = saved["handlers"]
        root.setLevel(saved["level"])
        for name, level in saved["quiet"].items():
            logging.getLogger(name).setLevel(level)


def test_attaches_one_handler_however_often_it_is_called(bare_root):
    root = bare_root()
    stream = io.StringIO()
    configure_process_logging("INFO", stream=stream)
    configure_process_logging("INFO", stream=stream)
    configure_process_logging("WARNING")
    assert len(root.handlers) == 1


def test_a_record_names_its_level_and_logger(bare_root):
    bare_root()
    stream = io.StringIO()
    configure_process_logging("INFO", stream=stream)
    logging.getLogger("openprogram.example").warning("disk %s", "full")
    line = stream.getvalue().strip()
    assert line.endswith(" WARNING openprogram.example: disk full")


def test_default_level_filters_lower_records(bare_root):
    root = bare_root()
    stream = io.StringIO()
    configure_process_logging("WARNING", stream=stream)
    logging.getLogger("openprogram.example").info("hidden")
    assert stream.getvalue() == ""
    assert root.level == logging.WARNING


def test_a_later_entry_point_moves_the_level(bare_root):
    root = bare_root()
    configure_process_logging("WARNING", stream=io.StringIO())
    configure_process_logging("INFO")
    assert root.level == logging.INFO


def test_env_overrides_the_default_level(bare_root, monkeypatch):
    root = bare_root()
    monkeypatch.setenv(ENV_LEVEL, "debug")
    configure_process_logging("WARNING", stream=io.StringIO())
    assert root.level == logging.DEBUG


def test_an_invalid_env_value_falls_back_to_the_default(bare_root, monkeypatch):
    root = bare_root()
    monkeypatch.setenv(ENV_LEVEL, "loud")
    configure_process_logging("WARNING", stream=io.StringIO())
    assert root.level == logging.WARNING


def test_noisy_dependencies_are_held_at_warning(bare_root):
    bare_root()
    configure_process_logging("INFO", stream=io.StringIO())
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING


def test_a_root_configured_elsewhere_is_left_alone(bare_root, monkeypatch):
    root = bare_root()
    theirs = logging.StreamHandler(io.StringIO())
    root.addHandler(theirs)
    root.setLevel(logging.ERROR)
    configure_process_logging("INFO")
    assert root.handlers == [theirs]
    assert root.level == logging.ERROR

    monkeypatch.setenv(ENV_LEVEL, "INFO")
    configure_process_logging("WARNING")
    assert root.handlers == [theirs]
    assert root.level == logging.INFO
