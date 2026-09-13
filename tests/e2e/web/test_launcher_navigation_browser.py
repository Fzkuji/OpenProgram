"""Rendered default launcher and production navigation controls."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import subprocess
import threading

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_default_launcher_all_destinations_return_and_forward(tmp_path: Path):
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "launcher.js"
    subprocess.run(["node", "-e", r'''
const {buildSync}=require("esbuild");
buildSync({stdin:{contents:`
import React from "react";
import {createRoot} from "react-dom/client";
import {NewTabPage} from "./components/center-tabs/new-tab-page";
import {PageNavigation} from "./components/center-tabs/page-navigation";
import {useCenterTabs} from "./lib/tabs/center-tabs-store";
import {setNavigate} from "./lib/navigate";
setNavigate(path=>window.history.pushState(null,"",path));
useCenterTabs.getState().openNewTabPage();
function App(){const tabs=useCenterTabs(s=>s.tabs); const tab=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));
return <><button onClick={()=>useCenterTabs.getState().openNewTabPage()}>New tab</button>{tabs.map((item,index)=><button key={item.id} onClick={()=>useCenterTabs.getState().setActive(item.id)}>Tab {index+1}</button>)}<output data-tabs>{JSON.stringify(tabs)}</output><PageNavigation/>{tab?.kind==="ntp"?<NewTabPage/>:<output data-destination>{tab?.page||tab?.kind}</output>}</>}
createRoot(document.getElementById("root")).render(<App/>);`,resolveDir:process.argv[1],loader:"tsx"},
bundle:true,format:"iife",platform:"browser",jsx:"automatic",loader:{".css":"empty"},outfile:process.argv[2],tsconfig:process.argv[1]+"/tsconfig.json"});
''', str(ROOT / "apps/web"), str(bundle)], cwd=ROOT, check=True, capture_output=True)
    (tmp_path / "index.html").write_text('<div id="root"></div><script src="launcher.js"></script>')
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                page = browser.new_page()
                page.add_init_script("localStorage.clear()")
                page.route("**/api/providers", lambda route: route.fulfill(json={"providers": []}))
                apps = [{"id": name.lower().replace(" ", "-"), "title": name, "scope": "global", "enabled": True} for name in ["Calculator", "Paper reader", "File analysis"]]
                page.route("**/api/applications", lambda route: route.fulfill(json={"applications": apps}))
                page.route("**/api/applications/*/open", lambda route: route.fulfill(json={"instance_id": "a" * 64}))
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                for name, destination in [("Files", "files"), ("New chat", "session"), ("Browser", "browser"), ("Terminal", "terminal"), ("Calculator", "application"), ("Paper reader", "application"), ("File analysis", "application")]:
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    expect(page.get_by_role("button", name="Back", exact=True)).to_be_disabled()
                    page.get_by_role("button", name=name, exact=True).click()
                    expect(page.locator("[data-destination]")).to_have_text(destination)
                    first = json.loads(page.locator("[data-tabs]").inner_text())[0]
                    page.get_by_role("button", name="New tab", exact=True).click()
                    expect(page.get_by_role("button", name="Back", exact=True)).to_be_disabled()
                    page.get_by_role("button", name=name, exact=True).click()
                    expect(page.locator("[data-destination]")).to_have_text(destination)
                    assert json.loads(page.locator("[data-tabs]").inner_text())[0] == first
                    page.get_by_role("button", name="Back", exact=True).click()
                    expect(page.get_by_role("button", name="Back", exact=True)).to_be_disabled()
                    assert json.loads(page.locator("[data-tabs]").inner_text())[0] == first
                    second = json.loads(page.locator("[data-tabs]").inner_text())[1]
                    page.get_by_role("button", name="Tab 1", exact=True).click()
                    page.get_by_role("button", name="Back", exact=True).click()
                    assert json.loads(page.locator("[data-tabs]").inner_text())[1] == second
                    expect(page.get_by_role("button", name="Files", exact=True)).to_be_visible()
                    expect(page.get_by_role("button", name="Back", exact=True)).to_be_disabled()
                    page.get_by_role("button", name="Forward", exact=True).click()
                    expect(page.locator("[data-destination]")).to_have_text(destination)
                    page.get_by_role("button", name="Back", exact=True).click()
                    page.get_by_role("button", name="Terminal", exact=True).click()
                    expect(page.get_by_role("button", name="Forward", exact=True)).to_be_disabled()
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
