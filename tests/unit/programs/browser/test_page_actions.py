"""The browser drivers' shared page actions: argument parsing, and the page scripts in a real Chromium."""
from __future__ import annotations

import json

import pytest

from openprogram.programs.applications.browser import page_actions as pa


# ----------------------------------------------------------------- arguments


def test_modifiers_accept_mac_and_pc_names_once_each():
    assert pa.modifiers("cmd+shift") == ["Meta", "Shift"]
    assert pa.modifiers("Ctrl, alt, ctrl") == ["Control", "Alt"]
    with pytest.raises(pa.ActionError) as error:
        pa.modifiers("hyper")
    assert error.value.reason_code == "invalid_modifiers"


def test_scroll_amount_keeps_its_old_meaning_without_a_direction():
    assert pa.scroll_delta({}) == (0, 500)
    assert pa.scroll_delta({"amount": -300}) == (0, -300)
    assert pa.scroll_delta({"amount": -300, "direction": "left"}) == (-300, 0)
    assert pa.scroll_delta({"amount": 200, "direction": "up"}) == (0, -200)


@pytest.mark.parametrize("arguments, code", [
    ({"mouse_button": "back"}, "invalid_mouse_button"),
    ({"click_count": 4}, "invalid_click_count"),
    ({"click_count": "two"}, "invalid_click_count"),
])
def test_click_options_are_checked(arguments, code):
    with pytest.raises(pa.ActionError) as error:
        pa.click_options(arguments)
    assert error.value.reason_code == code


def test_paste_offers_markdown_as_html_and_html_with_a_plain_fallback():
    assert pa.paste_payload({"text": "**hi**", "format": "md"})["html"].strip() == "<p><strong>hi</strong></p>"
    assert pa.paste_payload({"text": "<b>a</b> &amp; b", "format": "html"})["text"] == "a & b"
    assert pa.paste_payload({"text": "plain"}) == {"text": "plain", "html": ""}


# ----------------------------------------------------------------- MCP mapping


def test_inlined_scripts_throw_when_the_page_reports_failure():
    source = pa.bound_call(pa.INSERT_TEXT_SCRIPT, {"text": "值"}, require="ok")
    assert source.startswith("(element) => { const r = ") and "throw new Error" in source
    assert json.dumps({"text": "值"}, ensure_ascii=False) in source


# ----------------------------------------------------------------- scripts in Chromium

_PAGE = """
<input id=field value="hello world">
<div id=box style="height:60px;overflow:auto"><div id=inner style="height:600px">inside</div></div>
<p id=para>alpha <b>beta</b> gamma</p>
<div id=rich contenteditable>start</div>
<button id=btn>go</button>
<script>
  window.events = [];
  for (const type of ["click", "dblclick", "contextmenu", "auxclick", "mousedown"])
    btn.addEventListener(type, e => events.push([type, e.detail, e.button, e.shiftKey]));
</script>
"""


@pytest.fixture(scope="module")
def page():
    sync = pytest.importorskip("playwright.sync_api")
    with sync.sync_playwright() as runner:
        try:
            browser = runner.chromium.launch()
        except Exception as exc:  # no browser downloaded on this machine
            pytest.skip(f"Chromium is not available: {exc}")
        tab = browser.new_page()
        yield tab
        browser.close()


@pytest.fixture
def fresh(page):
    page.set_content(_PAGE)
    return page


def test_pointer_script_dispatches_double_right_and_modifier_clicks(fresh):
    button = fresh.locator("#btn")
    button.evaluate(pa.POINTER_SCRIPT, {"button": "left", "count": 2, "flags": {"shiftKey": True}})
    button.evaluate(pa.POINTER_SCRIPT, {"button": "right", "count": 1, "flags": {}})
    events = fresh.evaluate("events")
    assert ["click", 1, 0, True] in events and ["click", 2, 0, True] in events
    assert ["dblclick", 2, 0, True] in events and ["contextmenu", 1, 2, False] in events


def test_scroll_script_scrolls_the_nearest_scrollable_container(fresh):
    result = fresh.locator("#inner").evaluate(pa.SCROLL_SCRIPT, {"dx": 0, "dy": 100})
    assert result == {"moved": True, "container": "div"}
    assert fresh.evaluate("box.scrollTop") == 100


def test_insert_text_appends_at_the_caret_and_needs_a_focus(fresh):
    field = fresh.locator("#field")
    field.evaluate("el => { el.focus(); el.setSelectionRange(5, 5); }")
    assert field.evaluate(pa.INSERT_TEXT_SCRIPT, {"text": ","}) == {"ok": True}
    assert fresh.evaluate("field.value") == "hello, world"
    fresh.evaluate("document.activeElement.blur()")
    assert fresh.evaluate(f"(o) => ({pa.INSERT_TEXT_SCRIPT.strip()})(null, o)", {"text": "x"}) == {
        "ok": False, "reason_code": "focus_required"}


def test_paste_inserts_rich_text_or_lets_the_page_handle_it(fresh):
    rich = fresh.locator("#rich")
    rich.evaluate("el => { el.focus(); getSelection().selectAllChildren(el); getSelection().collapseToEnd(); }")
    result = rich.evaluate(pa.PASTE_SCRIPT, pa.paste_payload({"text": "**bold**", "format": "md"}))
    assert result == {"ok": True, "handled_by_page": False, "rich": True}
    assert "<strong>bold</strong>" in fresh.evaluate("rich.innerHTML")
    fresh.evaluate("rich.addEventListener('paste', e => { window.pasted = e.clipboardData.getData('text/plain'); e.preventDefault(); })")
    assert rich.evaluate(pa.PASTE_SCRIPT, {"text": "via page", "html": ""}) == {"ok": True, "handled_by_page": True}
    assert fresh.evaluate("window.pasted") == "via page"


# ----------------------------------------------------------------- evaluate

@pytest.mark.parametrize("script, part", [
    ("document.title", 'const s = "document.title";'),
    ("a.click(); 'done'", 'const s = "a.click(); \'done\'";'),
    ("() => location.href", "const run = (() => location.href\n);"),
    ("async () => 2", "const run = (async () => 2\n);"),
    ("function () { return 3 }", "const run = (function () { return 3 }\n);"),
])
def test_evaluate_calls_a_function_and_runs_anything_else_as_a_console_would(script, part):
    source = pa.evaluate_source({"script": script})
    assert source.startswith("(element) => {") and part in source and "30 * 1000" in source


def test_evaluate_needs_a_bounded_script_and_bounds_its_result():
    for script, code in (("", "script_required"), ("x" * (pa.MAX_SCRIPT_CHARS + 1), "script_too_long")):
        with pytest.raises(pa.ActionError) as error:
            pa.evaluate_source({"script": script})
        assert error.value.reason_code == code
    assert pa.evaluation_text({"k": "值"}) == '{"k": "值"}' and pa.evaluation_text(None) == "undefined"
    assert pa.evaluation_text("x" * (pa.MAX_RESULT_CHARS + 5)).endswith("[truncated, 5 more characters]")
