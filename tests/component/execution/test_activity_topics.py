"""Sorting a conversation's messages into topics, and the chart's short steps."""
from __future__ import annotations

import json
import threading

from openprogram.runtime.conversation import topics
from openprogram.runtime.conversation.topics import Ask, annotate_runs, read_book, sort_asks


def ask(number, text):
    return Ask(f"u{number}", text)


def answering(monkeypatch, *replies):
    """The model's answers in order; each call is recorded."""
    calls = []
    answers = list(replies)

    def model(book, previous, asks):
        calls.append((previous, [item.message_id for item in asks]))
        return answers.pop(0) if answers else None

    monkeypatch.setattr(topics, "_ask_model", model)
    return calls


def test_a_topic_that_returns_keeps_its_identity(tmp_path, monkeypatch):
    asks = [ask(1, "deploy the service"), ask(2, "make the figure bigger"), ask(3, "deploy it again")]
    answering(monkeypatch, ["Deployment", "Figures", "deployment"])
    assert sort_asks(tmp_path, asks) is True
    book = read_book(tmp_path)
    assert [topic["title"] for topic in book.topics] == ["Deployment", "Figures"]
    assert book.messages["u1"] == book.messages["u3"] != book.messages["u2"]


def test_a_short_reply_stays_with_the_message_before_it_and_costs_no_call(tmp_path, monkeypatch):
    calls = answering(monkeypatch, ["Deployment"])
    assert sort_asks(tmp_path, [ask(1, "deploy the service")]) is True
    assert sort_asks(tmp_path, [ask(1, "deploy the service"), ask(2, "好的")]) is True
    assert len(calls) == 1
    book = read_book(tmp_path)
    assert book.messages["u2"] == book.messages["u1"]


def test_a_chat_that_opens_with_small_talk_still_gets_a_topic(tmp_path, monkeypatch):
    calls = answering(monkeypatch, ["Greeting"])
    assert sort_asks(tmp_path, [ask(1, "hi")]) is True
    assert calls == [(None, ["u1"])]


def test_a_failed_call_leaves_messages_unsorted_for_the_next_pass(tmp_path, monkeypatch):
    answering(monkeypatch, None)
    assert sort_asks(tmp_path, [ask(1, "deploy the service")]) is False
    assert read_book(tmp_path).messages == {}
    calls = answering(monkeypatch, ["Deployment"])
    assert sort_asks(tmp_path, [ask(1, "deploy the service")]) is True
    assert len(calls) == 1


def test_a_backfill_goes_in_batches_and_passes_the_topic_before_each(tmp_path, monkeypatch):
    asks = [ask(number, f"message number {number}") for number in range(45)]
    calls = answering(monkeypatch, ["One"] * 40, ["Two"] * 5)
    assert sort_asks(tmp_path, asks) is True
    assert [len(batch) for _, batch in calls] == [40, 5]
    assert calls[1][0] == "One"


def test_a_damaged_file_reads_as_nothing_sorted(tmp_path):
    (tmp_path / topics.TOPICS_FILE).write_text("{not json", encoding="utf-8")
    assert read_book(tmp_path).messages == {}


def test_runs_get_their_topic_and_order_and_unsorted_ones_none(tmp_path):
    (tmp_path / topics.TOPICS_FILE).write_text(json.dumps({
        "topics": [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}], "messages": {"u1": "t2"},
    }), encoding="utf-8")
    runs = [{"user_message_id": "u1"}, {"user_message_id": "other"}, {"user_message_id": None}]
    assert annotate_runs("done", tmp_path, runs, [Ask("u1", "x")]) is False
    assert (runs[0]["topic_id"], runs[0]["topic_title"], runs[0]["topic_order"]) == ("t2", "B", 1)
    assert runs[1]["topic_id"] is None and runs[2]["topic_title"] is None


def test_opening_a_chart_starts_one_backfill_and_a_failure_waits(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def stuck(session_id):
        started.set()
        release.wait(5)
        with topics._lock:
            topics._running.discard(session_id)
            topics._retry_at[session_id] = topics.time.monotonic() + 600

    monkeypatch.setattr(topics, "_sort_session", stuck)
    asks = [Ask("u1", "x")]
    assert annotate_runs("backfill", tmp_path, [], asks) is True
    assert started.wait(5)
    assert annotate_runs("backfill", tmp_path, [], asks) is True
    release.set()
    for _ in range(500):
        with topics._lock:
            if "backfill" not in topics._running:
                break
        topics.time.sleep(0.01)
    assert annotate_runs("backfill", tmp_path, [], asks) is False
    topics._retry_at.pop("backfill", None)
