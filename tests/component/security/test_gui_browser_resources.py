"""Script resource selection against real registry leases and isolated Python."""
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from tests.component.security.test_gui_agent import owned

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="strict macOS sandbox")


@pytest.fixture
def pages(monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser import BrowserPageController
    from openprogram.programs.workflow.browser.web_use_runtime import ControllerBackend, WebUseSessionRegistry, SUPPORTED_BACKENDS
    state = {"text": "before", "frame": 1}
    controllers, released = [], []
    def create():
        controller = BrowserPageController(browser_api=object())
        page = SimpleNamespace(viewport_size={"width": 1, "height": 1}, url="https://owned.invalid", title=lambda: "Owned", evaluate=lambda script: {}, inner_text=lambda selector: state["text"])
        controller._page = lambda: page
        controller._observe = lambda: {"frame_id": f'f{state["frame"]}', "text": state["text"]}
        controller._fresh = lambda frame: frame == f'f{state["frame"]}'
        def fill(text):
            state["text"] = text
            state["frame"] += 1
        controller._ref = lambda ref: (SimpleNamespace(fill=fill), "")
        controllers.append(controller)
        return controller
    registry = WebUseSessionRegistry(adapters={name: ControllerBackend(name, create) for name in SUPPORTED_BACKENDS}, binding_validator=lambda binding: {"ok": True}, binding_revision_resolver=lambda binding: {}, page_key_resolver=lambda binding: binding, release_context=lambda context: released.append(context))
    monkeypatch.setattr(surface_context, "release_bindings", lambda context: released.append(context))
    context = {"context_id": "owned-context", "surfaces": [{"surface_key": "s1", "binding_id": "owned-binding", "title": "Owned Page", "capabilities": ["observe", "act"]}]}
    yield registry, context, state, released
    registry.close_all()
    for controller in controllers: controller._owner.shutdown(wait=True)


def test_script_lists_acquires_and_uses_exact_page_then_scope_revokes(owned, pages):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    registry, context, state, released = pages
    with GuiAgentTools() as gui:
        with GuiBrowserResources(gui.broker, registry, context) as resources:
            async def use():
                result = await gui.tool.execute("call", {"code": f"catalog = {resources.handle!r}\nlisted = (await ui.call(catalog, 'list'))['value']\ntoken = listed['pages'][0]['page_context_token']\npage = (await ui.call(catalog, 'acquire', {{'page_context_token': token}}))['value']['json_data']\nprint(page['handle'])\nawait ui.call(page['handle'], 'type', {{'ref': 'field', 'text': 'after'}}, observation=page['frame_id'])"}, None, None)
                assert not result.is_error, result
                return result.details["json"]["stdout"].strip()
            handle = asyncio.run(use())
            assert state["text"] == "after"
            assert len(registry._sessions) == 1
        assert not registry._sessions and not registry._page_leases and not registry._page_capabilities
        result = asyncio.run(gui.tool.execute("late", {"code": f"await ui.call({handle!r}, 'observe')"}, None, None))
        assert result.is_error and "denied" in result.details["json"]["error"]
    assert released


def test_foreign_token_and_owner_cannot_be_selected_or_released(owned, pages):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    registry, context, state, released = pages
    foreign = registry.list_pages(context={"surfaces": [{"binding_id": "foreign-binding"}]}, owner_id="foreign")
    token = foreign["pages"][0]["page_context_token"]
    with GuiAgentTools() as gui:
        with GuiBrowserResources(gui.broker, registry, context) as resources:
            for args in [{"page_context_token": token}, {"page_context_token": token, "owner_id": "foreign"}]:
                result = asyncio.run(gui.tool.execute("call", {"code": f"await ui.call({resources.handle!r}, 'acquire', {args!r})"}, None, None))
                assert result.is_error
            assert state["text"] == "before" and not registry._sessions
        assert token in registry._page_capabilities
    registry.release_owner("foreign")


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_failed_initial_observation_keeps_images_and_releases_session(owned, pages, cleanup_failure):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    from openprogram.programs import ToolReturn
    registry, context, state, released = pages
    adapter = registry._adapters["open_claude_chrome"]
    create = adapter._controller_factory
    image = b"\x89PNG\r\n\x1a\nowned diagnostic"
    def failing_controller():
        controller = create()
        controller._observe = lambda: ToolReturn(text="owned failure", images=[image], json_data={"frame_id": "failed-frame", "ok": False, "reason_code": "fixture_failure"}, is_error=True)
        return controller
    adapter._controller_factory = failing_controller
    if cleanup_failure:
        original_close = adapter.close
        def fail_close(session):
            original_close(session)
            raise RuntimeError("secondary cleanup failure")
        adapter.close = fail_close
    with GuiAgentTools() as gui:
        with GuiBrowserResources(gui.broker, registry, context) as resources:
            result = asyncio.run(gui.tool.execute("call", {"code": f"c = {resources.handle!r}\nt = (await ui.call(c, 'list'))['value']['pages'][0]['page_context_token']\nawait ui.call(c, 'acquire', {{'page_context_token': t}})"}, None, None))
            assert result.is_error
            assert not registry._sessions and not registry._page_leases
            effect = owned.effects.list_unresolved("exec")[0]
            assert effect.receipt["value"]["json_data"]["reason_code"] == "fixture_failure"
            assert effect.receipt["value"]["text"] == "owned failure"
            if cleanup_failure:
                assert "secondary cleanup failure" in effect.receipt["value"]["json_data"]["resource_cleanup_error"]
            manifest = effect.receipt["images"][0]
            assert b"".join(owned.executions.get_state_blob("exec", ref)["payload"] for ref in manifest["chunks"]) == image


def test_scope_close_during_acquire_prevents_late_handle_and_cleans_lease(owned, pages):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    registry, context, state, released = pages
    entered, release = threading.Event(), threading.Event()
    adapter = registry._adapters["open_claude_chrome"]
    original = adapter.observe
    def delayed(session, args, *, before_dispatch=None):
        entered.set()
        assert release.wait(3)
        return original(session, args, before_dispatch=before_dispatch)
    adapter.observe = delayed
    with GuiAgentTools() as gui:
        resources = GuiBrowserResources(gui.broker, registry, context)
        async def use():
            task = asyncio.create_task(gui.tool.execute("call", {"code": f"c = {resources.handle!r}\nt = (await ui.call(c, 'list'))['value']['pages'][0]['page_context_token']\nawait ui.call(c, 'acquire', {{'page_context_token': t}})"}, None, None))
            assert await asyncio.to_thread(entered.wait, 2)
            with ThreadPoolExecutor(max_workers=1) as executor:
                close = executor.submit(resources.close)
                for _ in range(100):
                    if resources._closed: break
                    await asyncio.sleep(0.005)
                assert resources._closed
                release.set()
                result = await asyncio.wait_for(task, 2)
                close.result(timeout=2)
            assert result.is_error
        try:
            asyncio.run(use())
        finally:
            release.set()
            resources.close()
        assert not registry._sessions and not registry._page_leases and not registry._page_capabilities
        assert len(resources._handles) == 1


def test_page_token_is_single_use_and_acquired_handle_remains_listed(owned, pages):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    registry, context, state, released = pages
    with GuiAgentTools() as gui:
        with GuiBrowserResources(gui.broker, registry, context) as resources:
            code = f"c = {resources.handle!r}\nt = (await ui.call(c, 'list'))['value']['pages'][0]['page_context_token']\nr = await ui.call(c, 'acquire', {{'page_context_token': t}})\nprint((await ui.call(c, 'list'))['value'])"
            result = asyncio.run(gui.tool.execute("call", {"code": code}, None, None))
            assert not result.is_error
            import ast
            listed = ast.literal_eval(result.details["json"]["stdout"].strip())
            assert listed["pages"] == [] and len(listed["acquired"]) == 1
            result = asyncio.run(gui.tool.execute("again", {"code": "await ui.call(c, 'acquire', {'page_context_token': t})"}, None, None))
            assert result.is_error and "already been acquired" in result.details["json"]["error"]
            assert len(registry._sessions) == 1


@pytest.mark.parametrize("tool_return", [False, True])
def test_registry_stamps_actual_session_over_adapter_identity(owned, pages, tool_return):
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    from openprogram.programs import ToolReturn
    registry, context, state, released = pages
    adapter = registry._adapters["open_claude_chrome"]
    create = adapter._controller_factory
    def controller_with_bad_metadata():
        controller = create()
        def observe():
            metadata = {"frame_id": "f1", "web_session_id": "pending", "backend": "forged-backend"}
            return ToolReturn(json_data=metadata) if tool_return else metadata
        controller._observe = observe
        return controller
    adapter._controller_factory = controller_with_bad_metadata
    with GuiAgentTools() as gui:
        with GuiBrowserResources(gui.broker, registry, context) as resources:
            code = f"c = {resources.handle!r}\nt = (await ui.call(c, 'list'))['value']['pages'][0]['page_context_token']\np = (await ui.call(c, 'acquire', {{'page_context_token': t}}))['value']['json_data']\nprint(p['web_session_id'])\nprint(p['backend'])"
            result = asyncio.run(gui.tool.execute("call", {"code": code}, None, None))
            assert not result.is_error, result
            session_id, backend = result.details["json"]["stdout"].splitlines()
            assert session_id in registry._sessions and backend == "open_claude_chrome"


def test_constructor_cleanup_does_not_mask_inventory_cancellation(owned, pages, monkeypatch):
    from openprogram.agent import surface_context
    from openprogram.backend.gui_agent import GuiAgentTools
    from openprogram.backend.gui_browser_resources import GuiBrowserResources
    registry, context, state, released = pages
    original = registry.list_pages
    primary = asyncio.CancelledError("inventory cancelled")
    def fail_inventory(**kwargs):
        original(**kwargs)
        raise primary
    def fail_release(context):
        raise RuntimeError("secondary release failure")
    monkeypatch.setattr(registry, "list_pages", fail_inventory)
    monkeypatch.setattr(surface_context, "release_bindings", fail_release)
    with GuiAgentTools() as gui:
        with pytest.raises(asyncio.CancelledError) as caught:
            GuiBrowserResources(gui.broker, registry, context)
        assert caught.value is primary
    assert not registry._sessions and not registry._page_capabilities
