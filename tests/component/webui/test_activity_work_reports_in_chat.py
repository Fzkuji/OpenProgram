"""The work snapshot says which always-on reports reached the conversation."""
from __future__ import annotations

from types import SimpleNamespace

from openprogram.interfaces.server.routes.execution import conversation_work
from openprogram.runtime.context import session_db


class _Db:
    def __init__(self, ids: set[str] | None):
        self._ids = ids

    def read_index(self, _session_id, reader):
        if self._ids is None:
            return None
        return reader(SimpleNamespace(get=lambda key: key if key in self._ids else None))


def test_a_report_never_posted_is_marked(monkeypatch):
    monkeypatch.setattr(session_db, "default_db", lambda: _Db({"report_0123456789abcdefghij"}))
    reports = [{"id": "0123456789abcdefghijKLMN"}, {"id": "stuck"}]
    conversation_work._mark_reports_in_chat("s", reports)
    assert [row["in_chat"] for row in reports] == [True, False]


def test_an_unreadable_conversation_claims_nothing(monkeypatch):
    monkeypatch.setattr(session_db, "default_db", lambda: _Db(None))
    reports = [{"id": "a"}]
    conversation_work._mark_reports_in_chat("s", reports)
    assert "in_chat" not in reports[0]
