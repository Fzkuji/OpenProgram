"""Real browser acceptance for the application pane and its scoped bridge."""
from pathlib import Path
import re
import socket
import subprocess
from threading import Thread

from fastapi import FastAPI
from fastapi.responses import Response
import pytest
import uvicorn

from tests.support.radix_select import choose
from tests.support.waiting import wait_until

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_application_ui_isolated_and_state_survives_reopening(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from openprogram.programs._host.ui import catalog, state
    from openprogram.interfaces.server.routes.catalog import applications
    from openprogram.interfaces.server.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    monkeypatch.setenv("HOME", str(tmp_path))
    # Pure Web application: saving its state never creates a Python process.
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'application.json').write_text('{"id":"test.browser","title":"Browser app","version":"1","capabilities":["storage.app"]}')
    (source / 'index.html').write_text('''<!doctype html><title>Browser app</title>
<label for="note">Note</label><input id="note" disabled><button id="save" disabled>Save</button><output id="status"></output>
<script>
let version;
openprogramApp.load().then(s=>{version=s.version;document.getElementById('note').value=s.value.note||'';document.getElementById('note').disabled=false;document.getElementById('save').disabled=false;});
document.getElementById('save').onclick=async()=>{const s=await openprogramApp.save({note:document.getElementById('note').value},version);version=s.version;document.getElementById('status').textContent='Saved';};
</script>''')
    html = (source / 'index.html').read_text()
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    (source / 'ui.js').write_text("import {suffix} from './chunk.js';\n" + script.replace("='Saved'", "='Saved'+suffix"))
    (source / 'chunk.js').write_text("export const suffix='';")
    (source / 'index.html').write_text(html.split('<script>', 1)[0] + '<script type="module" src="ui.js"></script>')
    definition = catalog.install(str(source))
    instance = state.instance(definition)
    from openprogram.state.store.project import resolve_project
    project_one = tmp_path / 'project-one'
    project_two = tmp_path / 'project-two'
    project_one.mkdir()
    project_two.mkdir()
    projects = [resolve_project(project_one), resolve_project(project_two)]
    project_source = tmp_path / 'project-app'
    project_source.mkdir()
    for name in ('index.html', 'ui.js', 'chunk.js'):
        (project_source / name).write_bytes((source / name).read_bytes())
    (project_source / 'application.json').write_text('{"id":"test.project-browser","title":"Project notes","version":"1","scope":"project","capabilities":["storage.app"]}')
    catalog.install(str(project_source))
    bundle = tmp_path / 'pane.js'
    subprocess.run(['node', '-e', '''
const esbuild=require('esbuild');
esbuild.buildSync({stdin:{contents:'import React from "react"; import {createRoot} from "react-dom/client"; import {ApplicationTabPane} from "./components/center-tabs/pages/application-tab-pane"; import {NewTabPage} from "./components/center-tabs/pages/new-tab-page"; import {useCenterTabs} from "./lib/tabs/center-tabs-store"; function App(){const tab=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));return window.launch?React.createElement(React.Fragment,null,React.createElement(NewTabPage),tab?.applicationInstanceId?React.createElement(ApplicationTabPane,{instanceId:tab.applicationInstanceId}):null):React.createElement(ApplicationTabPane,{instanceId:window.instanceId});} createRoot(document.getElementById("root")).render(React.createElement(App));',resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});
''', str(ROOT / 'apps/web'), str(bundle)], cwd=ROOT, check=True, capture_output=True)
    app = FastAPI()
    applications.register(app)
    @app.get('/')
    async def shell():
        return Response(f'<div id="root"></div><script>window.instanceId="{instance["id"]}";</script><script src="/pane.js"></script>', media_type='text/html')
    @app.get('/launch')
    async def launcher():
        return Response('<div id="root"></div><script>window.launch=true;</script><script src="/pane.js"></script>', media_type='text/html')
    @app.get('/pane.js')
    async def script():
        return Response(bundle.read_bytes(), media_type='application/javascript')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    auth = OwnerAuthState.start(state_dir=tmp_path / 'auth', bind_host='127.0.0.1', port=port, allowed_origins=(), owner_principal_id='owner/install/0123456789abcdef')
    server = uvicorn.Server(uvicorn.Config(OwnerAuthMiddleware(app, auth_state=auth), log_level='error'))
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        assert wait_until(lambda: server.started, timeout=10)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([{'name':auth.cookie_name,'value':auth.cookie_value,'url':f'http://127.0.0.1:{port}','httpOnly':True,'sameSite':'Strict'}])
                page = context.new_page()
                page.goto(f'http://127.0.0.1:{port}/')
                frame = page.frame_locator('iframe')
                frame.get_by_label('Note').fill('persistent annotation')
                frame.get_by_role('button', name='Save', exact=True).click()
                expect(frame.locator('output')).to_have_text('Saved')
                child = page.frames[1]
                assert child.evaluate("() => {try {parent.document.body; return false;} catch {return true;}}")
                assert child.evaluate("async () => {try {await fetch('/api/applications'); return false;} catch {return true;}}")
                page.reload()
                expect(page.frame_locator('iframe').get_by_label('Note')).to_have_value('persistent annotation')
                page.goto(f'http://127.0.0.1:{port}/launch')
                page.get_by_role('button', name='Project notes', exact=True).click()
                choose(page.locator('#application-project'), f'{projects[0].name} — {projects[0].path}')
                page.get_by_role('button', name='Open application', exact=True).click()
                project_frame = page.frame_locator('iframe')
                project_frame.get_by_label('Note').fill('first project')
                project_frame.get_by_role('button', name='Save', exact=True).click()
                expect(project_frame.locator('output')).to_have_text('Saved')
                first_url = page.locator('iframe').get_attribute('src')
                page.reload()
                page.get_by_role('button', name='Project notes', exact=True).click()
                expect(page.locator('#application-project')).to_have_count(0)
                expect(page.frame_locator('iframe').get_by_label('Note')).to_have_value('first project')
                assert page.locator('iframe').get_attribute('src') == first_url
                # A second explicit binding makes the next no-context launch
                # ask which project to use; it must keep a separate database.
                page.evaluate("async id => {await fetch('/api/applications/test.project-browser/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:id})});}", projects[1].id)
                page.reload()
                page.get_by_role('button', name='Project notes', exact=True).click()
                choose(page.locator('#application-project'), f'{projects[1].name} — {projects[1].path}')
                page.get_by_role('button', name='Open application', exact=True).click()
                expect(page.frame_locator('iframe').get_by_label('Note')).to_have_value('')
                assert page.locator('iframe').get_attribute('src') != first_url

            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        auth.close()
        assert not thread.is_alive()


def _serve(directory):
    """Serve an example's UI directory: module scripts need an http origin."""
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f'http://127.0.0.1:{server.server_address[1]}/index.html'


def test_calculator_sample_works_in_application_sandbox():
    from playwright.sync_api import sync_playwright, expect
    server, url = _serve(ROOT / 'examples/applications/calculator/ui')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # The host bridge: history round-trips through storage.app.
            page.add_init_script("""
                window.saved={version:1,value:{}};
                window.openprogramApp={
                    load:async()=>window.saved,
                    save:async(value,version)=>{window.saved={value,version:version+1};return window.saved;},
                };
            """)
            page.goto(url)
            field = page.get_by_label('Expression')
            field.fill('7+8')
            field.press('Enter')
            expect(page.locator('#result')).to_have_text('15')
            wait_until(lambda: page.evaluate('window.saved.version') == 2)
            assert page.evaluate('window.saved.value.history[0].result') == 15
            field.fill('1÷0')
            field.press('Enter')
            expect(page.locator('#result')).to_have_text('Cannot divide by zero')
            expect(page.locator('#result')).to_have_class(re.compile(r'\berror\b'))
        finally:
            browser.close()
            server.shutdown()


def test_reader_lists_project_pdfs_through_the_bridge():
    from playwright.sync_api import sync_playwright, expect
    server, url = _serve(ROOT / 'examples/applications/pdf_editor/ui')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # The library is built from the backend's list_files run, which
            # finishes only after a status poll; nothing renders before that.
            page.add_init_script("""
                window.polls=0;
                window.openprogramApp={
                    load:async()=>({version:1,value:{}}),
                    save:async(value,version)=>({value,version:version+1}),
                    run:async(operation,input,key)=>({id:'run-'+operation,status:'running',events:[]}),
                    status:async(id,cursor)=>{
                        window.polls++;
                        return {id,status:'completed',events:[],result:{project:'demo',files:[
                            {path:'papers/attention.pdf',size:2048,modified:1700000000000},
                            {path:'notes.pdf',size:512,modified:1700000000000},
                        ]}};
                    },
                };
            """)
            page.goto(url)
            rows = page.get_by_role('option')
            expect(rows).to_have_count(2)
            expect(page.locator('#project-name')).to_have_text('PDF files in demo')
            expect(rows.first).to_contain_text('attention.pdf')
            assert page.evaluate('window.polls') >= 1
            page.get_by_label('Filter PDFs').fill('notes')
            expect(rows).to_have_count(1)
            expect(rows.first).to_contain_text('notes.pdf')
            page.get_by_label('Filter PDFs').fill('nothing')
            expect(page.locator('#file-list')).to_contain_text('No PDF matches this filter.')
        finally:
            browser.close()
            server.shutdown()
