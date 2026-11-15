"""Keyword ``tool_search`` ranks with Pi's BM25 (deferred-tools A8)."""
from __future__ import annotations

import asyncio

import pytest

from openprogram.programs._host import runtime as R
from openprogram.programs._host.runtime import function, reset_registry, restore_registry, snapshot_registry
from openprogram.programs._host.tool_rank import document, rank, tokenize


@pytest.fixture(autouse=True)
def _isolate_registry():
    saved = snapshot_registry()
    allowed = R._allowed_tool_names.set(None)
    loaded = R._request_loaded.set(frozenset())
    reset_registry()
    yield
    restore_registry(saved)
    R._request_loaded.reset(loaded)
    R._allowed_tool_names.reset(allowed)


def _names(query, docs, limit=5):
    return [name for name, _score in rank(query, docs, limit)]


def test_a_name_match_beats_a_mention_in_the_description():
    docs = [
        document("notes__append", "Append a line; you can send it by email later."),
        document("gmail__send", "Send an email message.", namespace="gmail"),
    ]
    assert _names("send email", docs)[0] == "gmail__send"


def test_a_stop_word_alone_matches_nothing():
    assert tokenize("a") == []
    assert _names("a", [document("anything", "a tool")]) == []


def test_plural_query_matches_the_singular_name():
    assert _names("issues", [document("linear__create_issue", "Create one."),
                             document("weather", "Forecast.")]) == ["linear__create_issue"]


def test_the_exact_name_ranks_first():
    docs = [document("thread_read_all", "Read every thread; read a thread; thread reader."),
            document("app__thread_read", "Open one.")]
    assert _names("thread_read", docs)[0] == "app__thread_read"


def test_a_schema_property_name_is_searched():
    docs = [document("calendar__create", "Create an event.",
                     {"type": "object", "properties": {"attendees": {"type": "array"}}}),
            document("weather", "Forecast.")]
    assert _names("attendees", docs) == ["calendar__create"]


def test_camel_case_and_cjk_are_split():
    assert tokenize("getWeatherForecast") == ["get", "weather", "forecast"]
    assert _names("日程", [document("calendar", "管理日程和会议"), document("weather", "天气")]) == ["calendar"]


def test_keyword_search_loads_the_ranked_tools_and_respects_plus_terms():
    @function(name="gmail__send", defer=True)
    def send(to: str) -> str:
        """Send an email message."""
        return "sent"

    @function(name="notes__append", defer=True)
    def append(text: str) -> str:
        """Append a note; you can also mail it later."""
        return "ok"

    text, loaded = R._tool_search_run("send mail")
    assert [t.name for t in loaded][0] == "gmail__send"
    assert "<functions>" in text
    _, filtered = R._tool_search_run("+notes mail")
    assert [t.name for t in filtered] == ["notes__append"]


def test_select_of_a_loaded_tool_returns_its_definition_and_records_it():
    @function(name="cron_probe", defer=True)
    def cron_probe(when: str) -> str:
        """Schedule a probe."""
        return "ok"

    R.note_request_loaded({"cron_probe"})
    result = asyncio.run(R._tool_search_execute("c1", {"select": "select:cron_probe"}, None, None))
    assert "Loaded 1 deferred tool" in result.content[0].text
    assert [entry["name"] for entry in result.details["loads"]] == ["cron_probe"]
