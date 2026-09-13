from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
pytestmark = pytest.mark.browser


def _bundle_control_bar(output: Path) -> None:
    script = r"""
const esbuild = require("esbuild");
const fs = require("fs");
const path = require("path");
const web = process.argv[1];
const outfile = process.argv[2];
esbuild.build({
  absWorkingDir: web,
  stdin: {
    contents: [
      'export { createElement } from "react";',
      'export { createRoot } from "react-dom/client";',
      'export { BrowserControlBar } from "./components/center-tabs/browser-control-bar.tsx";',
      'export { recordOperationCue, resetBrowserControl } from "./lib/browser/browser-control.ts";',
      'export { ingestBrowserResource, resetBrowserResources, setBrowserConnection } from "./lib/chat/session-resources.ts";',
    ].join("\n"),
    resolveDir: web,
    loader: "tsx",
  },
  bundle: true,
  format: "iife",
  globalName: "ControlBarBundle",
  platform: "browser",
  target: "es2022",
  jsx: "automatic",
  outfile,
  tsconfig: path.join(web, "tsconfig.json"),
  plugins: [{
    name: "css-modules",
    setup(build) {
      build.onLoad({ filter: /\.module\.css$/ }, (args) => {
        const css = fs.readFileSync(args.path, "utf8");
        const classes = {};
        for (const match of css.matchAll(/\.([A-Za-z_][\w-]*)/g)) classes[match[1]] = match[1];
        return {
          contents:
            "const id = " + JSON.stringify(args.path) + ";\n" +
            "if (typeof document !== 'undefined' && !document.getElementById(id)) {" +
            "  const style = document.createElement('style');" +
            "  style.id = id;" +
            "  style.textContent = " + JSON.stringify(css) + ";" +
            "  document.head.appendChild(style);" +
            "}" +
            "export default " + JSON.stringify(classes) + ";",
          loader: "js",
        };
      });
      build.onLoad({ filter: /\.css$/ }, (args) => {
        if (args.path.endsWith(".module.css")) return;
        const css = fs.readFileSync(args.path, "utf8");
        return {
          contents:
            "if (typeof document !== 'undefined') {" +
            "  const style = document.createElement('style');" +
            "  style.textContent = " + JSON.stringify(css) + ";" +
            "  document.head.appendChild(style);" +
            "}",
          loader: "js",
        };
      });
    },
  }],
}).then(() => process.exit(0)).catch((error) => {
  console.error(error);
  process.exit(1);
});
"""
    result = subprocess.run(
        ["node", "-e", script, str(WEB), str(output)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)


def _html() -> str:
    return """<!doctype html>
<html>
<body>
  <div id="root" style="padding: 96px 48px;"></div>
  <script src="/control-bar.js"></script>
  <script>
    localStorage.setItem("agentic_locale", "en");
    const {
      createElement, createRoot, BrowserControlBar,
      recordOperationCue, resetBrowserControl,
      ingestBrowserResource, resetBrowserResources, setBrowserConnection,
    } = ControlBarBundle;
    resetBrowserControl();
    resetBrowserResources();
    setBrowserConnection(true);
    ingestBrowserResource({
      id: "assoc-a", resource_id: "page-a", session_id: "a", conversation_session_id: "a",
      tab_id: "w:a", kind: "web", title: "Plans", target: "https://a.test",
      status: "open", source: "browser", control_state: "active", generation: 1,
      sequence: 1, execution_id: "exec-a",
    }, "a");
    recordOperationCue({
      resourceId: "page-a",
      generation: 1,
      operation: {
        id: "op-1", action: "click", phase: "acknowledged",
        frame_id: "frame-1", geometry_revision: 3,
        point: { x: 10, y: 20, width: 100, height: 80 },
      },
    });
    createRoot(document.getElementById("root")).render(createElement(BrowserControlBar, {
      resource: {
        id: "assoc-a", resourceId: "page-a", tabId: "w:a",
        conversationSessionId: "a", generation: 1, controlState: "active",
      },
      compact: true,
    }));
  </script>
</body>
</html>
"""


def test_web_operation_history_menu_opens_and_dismisses_in_the_same_page() -> None:
    from playwright.sync_api import expect, sync_playwright

    with tempfile.TemporaryDirectory(prefix="openprogram-control-menu-") as directory:
        bundle = Path(directory) / "control-bar.js"
        _bundle_control_bar(bundle)
        html = _html().encode("utf-8")
        js = bundle.read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.endswith(".js"):
                    body, content_type = js, "application/javascript"
                else:
                    body, content_type = html, "text/html"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        browser = None
        try:
            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    page = context.new_page()
                    page.set_viewport_size({"width": 800, "height": 600})
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    history = page.get_by_role("button", name="Operation history")
                    expect(history).to_have_count(0)
                    page.get_by_role(
                        "button",
                        name="Small draggable Agent button. Click to expand. Drag to move.",
                        exact=True,
                    ).click()
                    history.wait_for()
                    assert context.pages == [page]
                    button_box = history.bounding_box()

                    history.click()
                    menu = page.get_by_role("menu")
                    menu.wait_for()
                    item = page.get_by_role("menuitem", name="click · acknowledged")
                    assert item.get_attribute("aria-disabled") == "true"
                    assert page.locator("[data-native-view-occluder]").count() == 0
                    menu_box = menu.bounding_box()
                    assert button_box and menu_box
                    assert menu_box["y"] >= button_box["y"]
                    assert menu_box["x"] + menu_box["width"] >= button_box["x"]
                    assert context.pages == [page]
                    page.mouse.click(790, 10)
                    menu.wait_for(state="hidden")
                    assert context.pages == [page]

                    history.focus()
                    page.keyboard.press("Enter")
                    page.get_by_role("menu").wait_for()
                    page.keyboard.press("Escape")
                    assert page.get_by_role("menu").count() == 0
                    expect(history).to_be_focused()

                    history.focus()
                    page.keyboard.press("Space")
                    page.get_by_role("menu").wait_for()
                    page.keyboard.press("Escape")
                    assert page.get_by_role("menu").count() == 0
                    expect(history).to_be_focused()
                    assert context.pages == [page]
                finally:
                    if browser is not None:
                        browser.close()
                        browser = None
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
