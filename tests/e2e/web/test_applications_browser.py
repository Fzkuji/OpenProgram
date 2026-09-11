"""Real browser acceptance for the application pane and its scoped bridge."""
from pathlib import Path
import socket
import subprocess
from threading import Thread

from fastapi import FastAPI
from fastapi.responses import Response
import pytest
import uvicorn

from tests.support.waiting import wait_until

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def test_application_ui_isolated_and_state_survives_reopening(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from openprogram.programs._applications import catalog, state
    from openprogram.webui.routes import applications
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    monkeypatch.setenv("HOME", str(tmp_path))
    # Pure Web application: saving its state never creates a Python process.
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'application.json').write_text('{"id":"test.browser","title":"Browser app","version":"1","capabilities":["storage.app"]}')
    (source / 'index.html').write_text('''<!doctype html><title>Browser app</title>
<label for="note">Note</label><input id="note"><button id="save">Save</button><output id="status"></output>
<script>
let version;
openprogramApp.load().then(s=>{version=s.version;document.getElementById('note').value=s.value.note||'';});
document.getElementById('save').onclick=async()=>{const s=await openprogramApp.save({note:document.getElementById('note').value},version);version=s.version;document.getElementById('status').textContent='Saved';};
</script>''')
    html = (source / 'index.html').read_text()
    script = html.split('<script>', 1)[1].split('</script>', 1)[0]
    (source / 'ui.js').write_text("import {suffix} from './chunk.js';\n" + script.replace("='Saved'", "='Saved'+suffix"))
    (source / 'chunk.js').write_text("export const suffix='';")
    (source / 'index.html').write_text(html.split('<script>', 1)[0] + '<script type="module" src="ui.js"></script>')
    definition = catalog.install(str(source))
    instance = state.instance(definition)
    bundle = tmp_path / 'pane.js'
    subprocess.run(['node', '-e', '''
const esbuild=require('esbuild');
esbuild.buildSync({stdin:{contents:'import React from "react"; import {createRoot} from "react-dom/client"; import {ApplicationTabPane} from "./components/center-tabs/application-tab-pane"; createRoot(document.getElementById("root")).render(React.createElement(ApplicationTabPane,{instanceId:window.instanceId}));',resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});
''', str(ROOT / 'apps/web'), str(bundle)], cwd=ROOT, check=True, capture_output=True)
    app = FastAPI()
    applications.register(app)
    @app.get('/')
    async def shell():
        return Response(f'<div id="root"></div><script>window.instanceId="{instance["id"]}";</script><script src="/pane.js"></script>', media_type='text/html')
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
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        auth.close()
        assert not thread.is_alive()
