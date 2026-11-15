"""A popup opened by an Agent's Page joins that Page's conversation.

Without this the popup was a bare tab: missing from Resources and from the
Agent's page list, so the Agent started the same flow again in its own Page.
"""
import asyncio

import pytest

from openprogram.interfaces.server.ws_actions import webtab


class _WS:
    def __init__(self):
        from openprogram.runtime.permissions.authority import local_owner_authority
        self.scope = {"state": {"authority": local_owner_authority()}}
        self.sent = []

    async def send_text(self, payload: str):
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _clean_webtab():
    for registry in (webtab._pending, webtab._bindings, webtab._desktop_windows,
                     webtab._connection_revisions, webtab._page_revisions,
                     webtab._restore_jobs):
        registry.clear()
    yield
    for registry in (webtab._pending, webtab._bindings, webtab._desktop_windows,
                     webtab._connection_revisions, webtab._page_revisions,
                     webtab._restore_jobs):
        registry.clear()


def _agent_page(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path)
    owner = _WS()
    generation = webtab.ensure_connection_revision(owner)
    webtab._desktop_windows[owner] = "win"
    binding_id = webtab.register_binding(owner, "win", "opener", "target-opener")
    opener_key = webtab.binding_page_key(binding_id)
    BrowserResourceStore().retain(
        page_key=opener_key, window_id="win", tab_id="opener",
        connection_generation=generation, session_id="conv",
        conversation_session_id="conv", execution_id="exec-1", live=True,
    )
    return owner, opener_key


def _popup(ws, **overrides):
    command = {
        "window_id": "win", "tab_id": "popup", "opener_tab_id": "opener",
        "target_id": "target-popup", "url": "https://visa.test/apply",
        "title": "Apply",
    }
    asyncio.run(webtab.handle_webtab_popup(ws, {**command, **overrides}))


def test_a_popup_joins_the_conversation_of_the_page_that_opened_it(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore
    owner, opener_key = _agent_page(monkeypatch, tmp_path)
    emitted = []
    monkeypatch.setattr(
        "openprogram.programs.applications.browser.pages.emit_browser_resource",
        lambda row, *, page_key: emitted.append(row),
    )

    _popup(owner)

    store = BrowserResourceStore()
    popup_keys = store.page_keys_for_tab("win", "popup")
    assert len(popup_keys) == 1
    owned = [
        a for a in store.associations_for_page(popup_keys[0])
        if a["conversation_session_id"]
    ]
    assert [(a["conversation_session_id"], a["execution_id"]) for a in owned] == [
        ("conv", "exec-1"),
    ]
    assert [row["tab_id"] for row in emitted] == ["popup"], "Resources lists it"


def test_a_popup_from_another_window_or_an_unknown_opener_is_ignored(monkeypatch, tmp_path):
    from openprogram.programs.applications.browser.pages import BrowserResourceStore
    owner, _ = _agent_page(monkeypatch, tmp_path)

    _popup(owner, window_id="other")
    _popup(owner, opener_tab_id="nobody")
    _popup(owner, target_id="")

    assert BrowserResourceStore().page_keys_for_tab("win", "popup") == []
    assert BrowserResourceStore().page_keys_for_tab("other", "popup") == []
