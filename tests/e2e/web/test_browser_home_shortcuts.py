"""Homepage shortcut editor behavior with production components and CSS."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
pytestmark = pytest.mark.browser


@pytest.fixture(scope="module")
def homepage_url(tmp_path_factory):
    directory = tmp_path_factory.mktemp("browser-home-shortcuts")
    result = subprocess.run(["node", "-e", r'''
const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");
const {pathToFileURL} = require("url");
const postcss = require("postcss");
const web = process.argv[1];
(async () => {
  const {default: config} = await import(pathToFileURL(path.join(web, "postcss.config.mjs")));
  const plugins = Object.entries(config.plugins).map(([name, options]) => require(name)(options));
  await esbuild.build({
    stdin: {contents: `
import React from "react";
import {createRoot} from "react-dom/client";
import {AppRouterContext} from "next/dist/shared/lib/app-router-context.shared-runtime";
import {BrowserHomePage} from "./components/center-tabs/browser/browser-home-page";
import "./app/globals.css";
const paired = new URLSearchParams(location.search).has("paired");
createRoot(document.getElementById("root")).render(
  <AppRouterContext.Provider value={{push(){},replace(){},prefetch(){},back(){},forward(){},refresh(){}}}>
    <section data-home="first" style={{display:"flex",flex:1,minWidth:0}}><BrowserHomePage/></section>
    {paired && <section data-home="second" style={{display:"flex",flex:1,minWidth:0}}><BrowserHomePage/></section>}
  </AppRouterContext.Provider>);`, resolveDir:web, loader:"tsx"},
    define: {"process.env": "{}"}, bundle:true, format:"iife", platform:"browser",
    target:"es2022", jsx:"automatic", outfile:process.argv[2],
    tsconfig:path.join(web,"tsconfig.json"), loader:{".woff2":"file", ".woff":"file", ".ttf":"file"},
    plugins:[{name:"production-styles", setup(build) {
      build.onLoad({filter:/\.module\.css$/}, args => ({contents:fs.readFileSync(args.path,"utf8"),loader:"local-css",resolveDir:path.dirname(args.path)}));
      build.onLoad({filter:/\/app\/globals\.css$/}, async args => {
        const result = await postcss(plugins).process(fs.readFileSync(args.path,"utf8"),{from:args.path});
        return {contents:result.css,loader:"css",resolveDir:path.dirname(args.path)};
      });
    }}],
  });
})().catch(error => {console.error(error);process.exit(1);});
''', str(WEB), str(directory / "homepage.js")], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    (directory / "index.html").write_text('''<!doctype html>
<html data-theme="light" data-theme-mode="light"><head><meta charset="utf-8">
<link rel="stylesheet" href="homepage.css"></head><body>
<main id="root" style="display:flex;height:100vh"></main><script src="homepage.js"></script>
</body></html>''')

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


@pytest.fixture
def homepage(homepage_url):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 900, "height": 650}, reduced_motion="reduce")
            page.set_default_timeout(5000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script('localStorage.clear(); localStorage.setItem("agentic_locale", "en");')

            def route_request(route):
                if not route.request.url.startswith(homepage_url):
                    route.abort()
                elif "/api/" in route.request.url:
                    route.fulfill(json={})
                else:
                    route.continue_()

            page.route("**/*", route_request)
            page.goto(homepage_url)
            yield page
            assert not errors, errors
        finally:
            browser.close()


@pytest.mark.parametrize("dismiss", ["Cancel", "Close", "Escape", "outside", "toggle"])
def test_dismissal_clears_draft_and_preserves_focus(homepage, dismiss):
    from playwright.sync_api import expect

    trigger = homepage.locator('[data-home="first"]').get_by_role("button", name="Add", exact=True)
    trigger.click()
    editor = homepage.get_by_role("dialog", name="Add shortcut", exact=True)
    name = editor.get_by_role("textbox", name="Name (optional)", exact=True)
    address = editor.get_by_role("textbox", name="Address", exact=True)
    expect(name).to_be_focused()
    expect(editor.get_by_role("button", name="Add", exact=True)).to_be_disabled()
    name.fill("Unfinished shortcut")
    address.fill("draft.example.com")
    if dismiss in ("Cancel", "Close"):
        editor.get_by_role("button", name=dismiss, exact=True).click()
    elif dismiss == "Escape":
        address.press("Escape")
    elif dismiss == "outside":
        homepage.locator('[data-home="first"]').get_by_role("textbox", name="Address", exact=True).click()
    else:
        trigger.click()
    expect(editor).to_have_count(0)
    if dismiss == "outside":
        expect(homepage.locator('[data-home="first"]').get_by_role("textbox", name="Address", exact=True)).to_be_focused()
    else:
        expect(trigger).to_be_focused()
    trigger.click()
    expect(name).to_have_value("")
    expect(address).to_have_value("")
    expect(editor.get_by_role("button", name="Add", exact=True)).to_be_disabled()
    assert homepage.evaluate('localStorage.getItem("agentic_ntp_shortcuts")') is None


@pytest.mark.parametrize("submit_field,label", [("Name (optional)", "Example"), ("Address", "")])
def test_enter_saves_once_from_either_field_with_optional_name(homepage, submit_field, label):
    from playwright.sync_api import expect

    trigger = homepage.locator('[data-home="first"]').get_by_role("button", name="Add", exact=True)
    trigger.click()
    editor = homepage.get_by_role("dialog", name="Add shortcut", exact=True)
    editor.get_by_role("textbox", name="Name (optional)", exact=True).fill(label)
    editor.get_by_role("textbox", name="Address", exact=True).fill("  example.com/docs  ")
    editor.get_by_role("textbox", name=submit_field, exact=True).press("Enter")
    expect(editor).to_have_count(0)
    expect(trigger).to_be_focused()
    saved = json.loads(homepage.evaluate('localStorage.getItem("agentic_ntp_shortcuts")'))
    assert [item for item in saved if item["url"] == "https://example.com/docs"] == [
        {"url": "https://example.com/docs", "label": label or "example.com"},
    ]
    expect(homepage.locator('[data-home="first"] button').filter(has=homepage.get_by_text(label or "example.com", exact=True))).to_be_visible()
    trigger.click()
    expect(editor.get_by_role("textbox", name="Address", exact=True)).to_have_value("")


def test_ime_confirmation_does_not_submit_or_close_editor(homepage):
    from playwright.sync_api import expect

    homepage.get_by_role("button", name="Add", exact=True).click()
    editor = homepage.get_by_role("dialog", name="Add shortcut", exact=True)
    address = editor.get_by_role("textbox", name="Address", exact=True)
    address.fill("example.com")
    for key, key_code in [("Enter", 13), ("Escape", 27), ("Enter", 229), ("Escape", 229)]:
        prevented = address.evaluate("""(element, options) => {
            const event = new KeyboardEvent('keydown', {...options, bubbles: true, cancelable: true});
            element.dispatchEvent(event);
            return event.defaultPrevented;
        }""", {"key": key, "code": key, "keyCode": key_code, "isComposing": key_code != 229})
        assert prevented, "IME confirmation must cancel the native submit/dismiss default"
        expect(editor).to_be_visible()
        assert homepage.evaluate('localStorage.getItem("agentic_ntp_shortcuts")') is None
    address.press("Escape")
    expect(editor).to_have_count(0)


def test_multiple_homes_have_independent_labels_and_hidden_anchors(homepage, homepage_url):
    from playwright.sync_api import expect

    homepage.goto(homepage_url + "?paired=1")
    first = homepage.locator('[data-home="first"]')
    second = homepage.locator('[data-home="second"]')
    first.get_by_role("button", name="Add", exact=True).click()
    editor = homepage.get_by_role("dialog", name="Add shortcut", exact=True)
    first_id = editor.get_attribute("id")
    editor.get_by_role("textbox", name="Name (optional)", exact=True).fill("First draft")
    second.get_by_role("button", name="Add", exact=True).click()
    expect(editor).to_have_count(1)
    expect(editor.get_by_role("textbox", name="Name (optional)", exact=True)).to_have_value("")
    assert editor.get_attribute("id") != first_id
    second.evaluate("element => { element.style.display = 'none'; }")
    expect(editor).not_to_be_visible()
    first.get_by_role("button", name="Add", exact=True).click()
    expect(editor).to_be_visible()
    expect(editor.get_by_role("textbox", name="Name (optional)", exact=True)).to_have_value("")


@pytest.mark.parametrize("theme,width", [("light", 900), ("dark", 900), ("light", 320), ("dark", 320), ("beige-light", 900), ("beige-dark", 900)])
def test_editor_uses_theme_font_and_fits_narrow_viewports(homepage, theme, width):
    from playwright.sync_api import expect

    homepage.set_viewport_size({"width": width, "height": 650})
    homepage.evaluate("theme => { document.documentElement.dataset.theme = theme; document.documentElement.dataset.themeMode = theme.endsWith('light') ? 'light' : 'dark'; }", theme)
    homepage.get_by_role("button", name="Add", exact=True).click()
    editor = homepage.get_by_role("dialog", name="Add shortcut", exact=True)
    expect(editor).to_be_visible()
    editor.get_by_role("textbox", name="Name (optional)", exact=True).fill("OpenProgram")
    editor.get_by_role("textbox", name="Address", exact=True).fill("github.com/FuzzyKuji/OpenProgram")
    homepage.evaluate("document.fonts.ready")
    box = editor.bounding_box()
    assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["y"] >= 0 and box["y"] + box["height"] <= 650
    assert editor.evaluate("element => element.scrollWidth <= element.clientWidth")
    fonts = editor.locator("input, button, label").evaluate_all("elements => elements.map(element => getComputedStyle(element).fontFamily)")
    assert fonts and set(fonts) == {homepage.evaluate("getComputedStyle(document.body).fontFamily")}
    # The fields are the app's shared Input and keep its themed surface.
    inputs = editor.get_by_role("textbox")
    assert inputs.evaluate_all("elements => elements.every(element => element.dataset.slot === 'input')")
    backgrounds = inputs.evaluate_all("elements => elements.map(element => getComputedStyle(element).backgroundColor)")
    assert len(set(backgrounds)) == 1
    for field in ("Name (optional)", "Address"):
        field_box = editor.get_by_role("textbox", name=field, exact=True).bounding_box()
        assert field_box and field_box["width"] > 100 and field_box["x"] >= box["x"]
        assert field_box["x"] + field_box["width"] <= box["x"] + box["width"]
    evidence = os.environ.get("OPENPROGRAM_TEST_SCREENSHOT_DIR")
    if evidence:
        destination = Path(evidence)
        destination.mkdir(parents=True, exist_ok=True)
        homepage.screenshot(path=str(destination / f"homepage-shortcut-{theme}-{width}.png"))
    homepage.evaluate("document.documentElement.style.setProperty('--font-sans', 'Georgia, serif')")
    assert inputs.evaluate_all("elements => elements.every(element => getComputedStyle(element).fontFamily === 'Georgia, serif')")
