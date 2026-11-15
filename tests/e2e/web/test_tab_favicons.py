from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps/web"
pytestmark = pytest.mark.browser

def _bundle_tabs(output: Path) -> None:
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
      'export { flushSync } from "react-dom";',
      'export { TabFavicon } from "./components/center-tabs/strip/tab-favicon.tsx";',
      'export { createRoot } from "react-dom/client";',
      'export { WebTabPane } from "./components/center-tabs/browser/web-tab-pane.tsx";',
      'export { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";',
      'export { BookmarkBar } from "./components/center-tabs/browser/bookmark-bar.tsx";',
      'export { loadFavicon } from "./lib/browser/favicon-cache.ts";',
      'export { useCenterTabs } from "./lib/tabs/center-tabs-store.ts";',
      'export { TabItem, CompoundTabItem } from "./components/center-tabs/strip/tab-items.tsx";',
    ].join("\n"),
    resolveDir: web,
    loader: "tsx",
  },
  bundle: true,
  format: "iife",
  globalName: "TabBundle",
  platform: "browser",
  target: "es2022",
  define: {"process.env.NODE_ENV": JSON.stringify("test")},
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


@pytest.mark.parametrize("compound", [False, True])
def test_tab_favicons_remain_visible(tmp_path: Path, compound: bool) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "tabs.js"
    _bundle_tabs(bundle)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.route("https://tabs.test/", lambda route: route.fulfill(body='<div id="root"></div>', content_type="text/html"))
            page.goto("https://tabs.test/")
            page.evaluate("window.process = {env: {NODE_ENV: 'test'}}")
            page.add_script_tag(path=str(bundle))
            page.evaluate("""compound => {
              const {createElement: h, createRoot, TabItem, CompoundTabItem} = TabBundle;
              const root = createRoot(document.getElementById('root'));
              window.showIcon = url => {
                const tab = {id:'web-a',kind:'web',title:'Website',url:'https://site.test/',faviconUrl:url};
                const common = {onActivate(){},onFocusTab(){},onOpenMenu(){},onClose(){},onExited(){},onDragPointerDown(){},shiftX:0};
                root.render(compound ? h(CompoundTabItem, {...common,tabs:[tab,{id:'chat-b',kind:'session',title:'Chat'}],group:{id:'group',memberIds:['web-a','chat-b'],visibleIds:['web-a','chat-b'],focusedId:'web-a'},activeId:'web-a',focusedTabId:'web-a',closingIds:new Set()}) : h(TabItem,{...common,tab,active:true,tabStop:true,closing:false}));
              };
              window.iconURL = color => 'data:image/svg+xml;base64,'+btoa('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'+(color?'<rect width="16" height="16" fill="'+color+'"/>':'')+'</svg>');
              showIcon(iconURL(null));
            }""", compound)
            icon = page.locator('[class*="tabIcon"]').first
            expect(icon.locator('svg')).to_be_visible()
            # A loaded but transparent image must not replace the fallback.
            page.evaluate("TabBundle.loadFavicon(iconURL(null))")
            expect(icon.locator('svg')).to_be_visible()
            page.evaluate("showIcon(iconURL('red'))")
            expect(icon.locator('canvas')).to_be_visible()
            expect(icon.locator('svg')).to_have_count(0)
            page.evaluate("showIcon('data:image/png;base64,broken')")
            expect(icon.locator('svg')).to_be_visible()
            page.evaluate("showIcon(iconURL('blue'))")
            expect(icon.locator('canvas')).to_be_visible()
            expect(icon.locator('svg')).to_have_count(0)
            page.evaluate("showIcon('')")
            expect(icon.locator('svg')).to_be_visible()
        finally:
            browser.close()


def test_bookmark_bar_icons_overflow_and_all_bookmarks(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    bundle = tmp_path / "bookmarks.js"
    _bundle_tabs(bundle)
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            is_icon = self.path in ('/favicon.ico', '/nested-icon.svg')
            if is_icon:
                requests.append(dict(self.headers))
            body = ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>' if is_icon else '<div id="root"></div>').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'image/svg+xml' if is_icon else 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    # Playwright intentionally aborts /favicon.ico when request routing is on.
    # Serve a real owned loopback endpoint to exercise the production URL unchanged.
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 650, "height": 450})
                page.goto(base + '/')
                page.evaluate("window.process = {env: {NODE_ENV: 'test'}}")
                page.add_script_tag(path=str(bundle))
                page.evaluate("""base => {
                  const leaf = (id, title) => ({kind:'bookmark', id, title, url:base+'/'+id});
                  const root = {kind:'folder',id:'root',title:'',children:[
                    {kind:'folder',id:'bar',title:'Bookmarks bar',children:[leaf('first','Website'),
                      {kind:'folder',id:'folder',title:'Folder',children:[{...leaf('nested','Nested website'), faviconUrl:base+'/nested-icon.svg'}]},
                      ...Array.from({length:12},(_,i)=>leaf('more'+i,'Long bookmark '+i))]},
                    {kind:'folder',id:'other',title:'Other bookmarks',children:[leaf('other-site','Other website')]}
                  ]};
                  localStorage.setItem('openprogram.bookmarks', JSON.stringify({version:2,root}));
                  localStorage.setItem('agentic_locale','en');
                  window.bookmarkTree=root;
                  const {createElement:h,createRoot,BookmarkBar}=TabBundle;
                  createRoot(document.getElementById('root')).render(h('div',{className:'webPane'},
                    h('div',{className:'webChrome'},
                      h('div',{className:'webToolbar'},h('div',{className:'webAddressBox'},h('span',{style:{position:'relative',flex:1,minWidth:0,alignSelf:'stretch',display:'flex'}},h('input',{className:'webAddress','aria-label':'Address'})))),
                      h(BookmarkBar,{ownerId:'test',onNavigate(url){window.navigated=url}}))));
                }""", base)
                website = page.get_by_role('button', name='Website', exact=True)
                expect(website.locator('canvas')).to_be_visible()
                assert website.bounding_box()['width'] > 50
                assert website.bounding_box()['height'] == 24
                assert page.get_by_role('textbox',name='Address').bounding_box()['height'] == 30
                assert requests and not requests[0].get('Referer')
                more = page.get_by_role('button', name='Show hidden bookmarks', exact=True)
                all_bookmarks = page.get_by_role('button', name='All bookmarks', exact=True)
                expect(more).to_be_visible()
                expect(all_bookmarks).to_be_visible()
                assert more.bounding_box()['x'] < all_bookmarks.bounding_box()['x']
                expect(page.get_by_role('button', name='Other bookmarks', exact=True)).to_have_count(0)
                overflow_icon = page.locator('.bookmarkBarOverflowed canvas').last
                expect(overflow_icon).to_have_count(1)
                expect(overflow_icon).to_be_hidden()
                folder = page.get_by_role('button', name='Folder', exact=True)
                folder.click()
                nested_icon = page.get_by_role('menuitem', name='Nested website', exact=True).locator('.tabFavicon')
                expect(nested_icon).to_be_visible()
                expect(nested_icon).to_have_css("opacity", "1")
                loaded_requests = len(requests)
                page.keyboard.press('Escape')
                expect(page.locator('[role=menuitem]').filter(has_text='Nested website')).to_have_count(0)
                folder.click()
                expect(nested_icon).to_be_visible()
                expect(nested_icon).to_have_css("opacity", "1")
                assert len(requests) == loaded_requests, 'Reopening a folder must reuse decoded icons, even with no-store responses'
                page.keyboard.press('Escape')
                more.click()
                hidden = page.get_by_role('menuitem', name='Long bookmark 11', exact=True)
                expect(hidden.locator('canvas')).to_be_visible()
                hidden.click()
                assert page.evaluate('window.navigated') == base + '/more11'
                all_bookmarks.click()
                assert page.evaluate("TabBundle.useCenterTabs.getState().tabs.some(t=>t.kind==='builtin' && t.page==='bookmarks')")
                # A changed saved icon must recover; bad saved data tries the site's icon.
                page.evaluate("""() => {
                  bookmarkTree.children[0].children[0].faviconUrl='data:image/png;base64,broken';
                  localStorage.setItem('openprogram.bookmarks',JSON.stringify({version:2,root:bookmarkTree}));
                  window.dispatchEvent(new Event('openprogram:bookmarks-changed'));
                }""")
                expect(website.locator('canvas')).to_be_visible()
                assert website.locator('canvas').evaluate('(canvas) => Array.from(canvas.getContext("2d").getImageData(16,16,1,1).data)') == [255, 0, 0, 255]
                page.set_viewport_size({"width":1100,"height":450})
                expect(all_bookmarks).to_be_visible()
                assert more.bounding_box()['x'] < all_bookmarks.bounding_box()['x']
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_bookmark_uses_current_tab_icon_for_duplicate_urls(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    bundle = tmp_path / "tabs.js"
    _bundle_tabs(bundle)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.fulfill(
                body='<div id="root"></div>', content_type="text/html",
            ))
            page.goto("http://bookmark.test/")
            page.evaluate("window.process = {env: {NODE_ENV: 'test'}}")
            page.add_script_tag(path=str(bundle))
            page.evaluate("""() => {
              localStorage.setItem('agentic_locale', 'en');
              const {createElement: h, createRoot, WebTabPane, useCenterTabs, AppRouterContext} = TabBundle;
              const url = 'http://bookmark.test/site';
              const first = useCenterTabs.getState().ensureWebTab(url);
              const current = useCenterTabs.getState().openPopupWebTab(url, first);
              const icon = color => 'data:image/svg+xml;base64,' + btoa(
                '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="' + color + '"/></svg>');
              window.bookmarkFixture = {current, initial: icon('blue'), updated: icon('red')};
              useCenterTabs.getState().updateWebTab(current, {faviconUrl: bookmarkFixture.initial, title: 'Current popup'});
              createRoot(document.getElementById('root')).render(h(AppRouterContext.Provider, {
                value: {push(){}, replace(){}, prefetch(){}, refresh(){}, back(){}, forward(){}}
              }, h(WebTabPane, {tabId: current, url})));
            }""")
            page.get_by_role("button", name="Bookmark", exact=True).click()
            page.wait_for_function("""() => JSON.parse(localStorage.getItem('openprogram.bookmarks'))
              .root.children[0].faviconUrl === bookmarkFixture.initial""")
            page.evaluate("""() => TabBundle.useCenterTabs.getState().updateWebTab(
              bookmarkFixture.current, {faviconUrl: bookmarkFixture.updated})""")
            page.wait_for_function("""() => JSON.parse(localStorage.getItem('openprogram.bookmarks'))
              .root.children[0].faviconUrl === bookmarkFixture.updated""")
            saved = page.evaluate("JSON.parse(localStorage.getItem('openprogram.bookmarks')).root.children[0]")
            assert saved['title'] == 'Current popup'
            assert saved['url'] == 'http://bookmark.test/site'
        finally:
            browser.close()


def test_cached_icon_remount_has_no_placeholder(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bundle = tmp_path / "cached-tabs.js"
    _bundle_tabs(bundle)
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.route("http://icons.test/", lambda route: route.fulfill(body='<div id="root"></div>', content_type="text/html"))
            page.goto("http://icons.test/")
            page.evaluate("window.process = {env: {NODE_ENV: 'test'}}")
            page.add_script_tag(path=str(bundle))
            page.evaluate("""() => {
              const {createElement:h,createRoot,TabFavicon,flushSync}=TabBundle;
              const root=createRoot(document.getElementById('root'));
              const url='data:image/svg+xml;base64,'+btoa('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>');
              window.remount=()=>{
                flushSync(()=>root.render(null));
                flushSync(()=>root.render(h(TabFavicon,{url})));
                return Boolean(document.querySelector('#root svg'));
              };
              remount();
            }""")
            expect(page.locator('#root .tabFavicon')).to_be_visible()
            expect(page.locator('#root svg')).to_have_count(0)
            assert page.evaluate('remount()') is False, 'A decoded icon must be available in the first remount frame'
        finally:
            browser.close()
