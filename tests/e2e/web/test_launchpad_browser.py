"""The new-tab Launchpad: pages, search, folders made by dragging, edit mode."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def _bundle(out: Path) -> None:
    subprocess.run(["node", "-e", r'''
const {buildSync}=require("esbuild");
buildSync({stdin:{contents:`
import React from "react";
import {createRoot} from "react-dom/client";
import {NewTabPage} from "./components/center-tabs/pages/new-tab-page";
import {useCenterTabs} from "./lib/tabs/center-tabs-store";
useCenterTabs.getState().openNewTabPage();
function App(){const tab=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));
return <div style={{display:"flex",width:"100vw",height:"100vh"}}>{tab?.kind==="ntp"?<NewTabPage/>:<output data-destination>{tab?.page||tab?.kind}</output>}</div>}
createRoot(document.getElementById("root")).render(<App/>);`,resolveDir:process.argv[1],loader:"tsx"},
bundle:true,format:"iife",platform:"browser",jsx:"automatic",loader:{".css":"local-css"},outdir:process.argv[2],entryNames:"launchpad",
tsconfig:process.argv[1]+"/tsconfig.json"});
// The components lay themselves out with Tailwind utilities; generate them
// from the app's own configuration, as the Next build does.
const fs=require("node:fs"),path=require("node:path");
const globals=path.join(process.argv[1],"app/globals.css");
require("postcss")([require("@tailwindcss/postcss")({base:process.argv[1]})]).process(fs.readFileSync(globals,"utf8"),{from:globals})
  .then(result=>fs.writeFileSync(path.join(process.argv[2],"global.css"),result.css))
  .catch(error=>{console.error(error);process.exit(1)});
''', str(ROOT / "apps/web"), str(out)], cwd=ROOT, check=True, capture_output=True)
    (out / "index.html").write_text(
        '<!doctype html><html data-theme-mode="dark"><meta charset="utf-8">'
        '<link rel="stylesheet" href="global.css"><link rel="stylesheet" href="launchpad.css"><style>body{margin:0}</style>'
        '<div id="root"></div><script src="launchpad.js"></script>'
    )


@pytest.fixture
def launchpad(tmp_path: Path):
    from playwright.sync_api import sync_playwright

    _bundle(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    apps = [{"id": f"app{n}", "title": f"App {n:02d}", "icon": "calculator", "scope": "global", "enabled": True} for n in range(36)]
    patches = []
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            page = browser.new_page(viewport={"width": 1200, "height": 760})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/providers", lambda route: route.fulfill(json={"providers": []}))

            def applications(route):
                if route.request.method == "PATCH":
                    patches.append((route.request.url.rsplit("/", 1)[-1], json.loads(route.request.post_data)))
                    apps[:] = [app for app in apps if app["id"] != patches[-1][0]]
                    return route.fulfill(json={"ok": True})
                route.fulfill(json={"applications": apps})

            page.route("**/api/applications", applications)
            page.route("**/api/applications/*", applications)
            page.goto(f"http://127.0.0.1:{server.server_port}/")
            page.get_by_role("button", name="App 00", exact=True).wait_for()
            yield page, patches
            assert errors == []
            browser.close()
    finally:
        server.shutdown()


def _tile_center(page, name):
    box = page.get_by_role("button", name=name, exact=True).locator("span").first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _drag(page, source, target_xy, settle=700):
    x, y = _tile_center(page, source)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 12, y + 6, steps=3)
    page.mouse.move(*target_xy, steps=12)
    page.wait_for_timeout(settle)
    page.mouse.up()
    page.wait_for_timeout(400)


def _layout(page):
    return json.loads(page.evaluate("localStorage.getItem('launchpad_layout')") or "null")


def test_forty_icons_fill_two_pages_and_the_wheel_and_dots_turn_them(launchpad):
    from playwright.sync_api import expect

    page, _ = launchpad
    # 4 built-ins + 36 applications = 40 icons: a full 7 x 5 page and 5 more.
    dots = page.get_by_role("button", name="Page 2", exact=True)
    expect(dots).to_be_visible()
    expect(page.get_by_role("button", name="Page 1", exact=True)).to_have_attribute("aria-current", "page")
    page.mouse.move(600, 400)
    page.mouse.wheel(0, 120)
    expect(dots).to_have_attribute("aria-current", "page")
    expect(page.get_by_role("button", name="App 35", exact=True)).to_be_in_viewport()
    page.get_by_role("button", name="Page 1", exact=True).click()
    expect(page.get_by_role("button", name="Page 1", exact=True)).to_have_attribute("aria-current", "page")


_TRACKPAD_SWIPE = r"""
async (sign) => {
  const root = document.querySelector('[data-testid=launchpad]');
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const send = d => root.dispatchEvent(new WheelEvent('wheel', { deltaX: sign * d, bubbles: true }));
  // The fingers' phase ramps up, then momentum keeps decaying after they lift.
  for (const d of [1, 2, 4, 7, 11, 15, 18, 20, 22, 20, 18, 15]) { send(d); await wait(8); }
  for (let v = 30; v > 0.6; v *= 0.9) { send(v); await wait(16); }
  await wait(600);
}"""


def test_one_trackpad_swipe_turns_one_page_however_long_its_momentum(launchpad):
    from playwright.sync_api import expect

    page, _ = launchpad
    page.evaluate(_TRACKPAD_SWIPE, 1)
    expect(page.get_by_role("button", name="Page 2", exact=True)).to_have_attribute("aria-current", "page")
    # Past the last page the same swipe only nudges; it never springs back to page 1.
    page.evaluate(_TRACKPAD_SWIPE, 1)
    expect(page.get_by_role("button", name="Page 2", exact=True)).to_have_attribute("aria-current", "page")
    page.evaluate(_TRACKPAD_SWIPE, -1)
    expect(page.get_by_role("button", name="Page 1", exact=True)).to_have_attribute("aria-current", "page")


def test_typing_searches_and_enter_opens_the_first_result(launchpad):
    from playwright.sync_api import expect

    page, _ = launchpad
    page.get_by_test_id("launchpad").focus()
    page.keyboard.type("termi")
    expect(page.get_by_role("button", name="Terminal", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Files", exact=True)).to_have_count(0)
    page.keyboard.press("Enter")
    expect(page.locator("[data-destination]")).to_have_text("terminal")


def test_dropping_an_icon_on_another_makes_a_folder_that_can_be_renamed_and_emptied(launchpad):
    from playwright.sync_api import expect

    page, _ = launchpad
    _drag(page, "App 01", _tile_center(page, "App 00"))
    folder = _layout(page)["pages"][0][4]
    assert folder["kind"] == "folder" and folder["items"] == ["app:app0", "app:app1"]
    page.keyboard.press("Escape")

    page.get_by_role("button", name="Folder", exact=True).click()
    title = page.get_by_role("textbox", name="Folder name")
    title.fill("Tools")
    title.press("Enter")
    assert _layout(page)["pages"][0][4]["name"] == "Tools"

    # Dragging an icon out of the panel closes the folder and drops it on the page;
    # the folder, left with one icon, turns back into that icon.
    fx, fy = _tile_center(page, "Files")
    page.keyboard.press("Escape")
    page.get_by_role("button", name="Tools", exact=True).click()
    _drag(page, "App 01", (fx - 50, fy))
    expect(page.get_by_role("textbox", name="Folder name")).to_have_count(0)
    ids = [entry["id"] for entry in _layout(page)["pages"][0]]
    assert ids[0] == "app:app1" and "app:app0" in ids and not any(i.startswith("folder:") for i in ids)


def test_dragging_between_icons_reorders_them(launchpad):
    page, _ = launchpad
    x, y = _tile_center(page, "Files")
    # The gap just left of New chat's icon, not its centre, means "insert here".
    nx, ny = _tile_center(page, "New chat")
    _drag(page, "Browser", (nx - 40, ny))
    assert [entry["id"] for entry in _layout(page)["pages"][0][:3]] == ["builtin:files", "builtin:browser", "builtin:chat"]


def test_long_press_enters_edit_mode_where_x_hides_an_application(launchpad):
    from playwright.sync_api import expect

    page, patches = launchpad
    x, y = _tile_center(page, "Files")
    page.mouse.move(x, y)
    page.mouse.down()
    page.wait_for_timeout(800)
    page.mouse.up()
    expect(page.get_by_role("button", name="Done", exact=True)).to_be_visible()
    # Built-ins stay; applications can be taken off the Launchpad.
    expect(page.get_by_role("button", name="Remove Files")).to_have_count(0)
    page.get_by_role("button", name="Remove App 00").click()
    expect(page.get_by_role("button", name="App 00", exact=True)).to_have_count(0)
    assert patches == [("app0", {"hidden": True})]
    page.keyboard.press("Escape")
    expect(page.get_by_role("button", name="Done", exact=True)).to_have_count(0)
