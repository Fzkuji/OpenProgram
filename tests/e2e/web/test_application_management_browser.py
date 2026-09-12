"""Owner application management with real registration, storage, and launcher."""
import json
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


def test_install_manage_launch_and_reinstall_preserves_data(tmp_path, monkeypatch):
    from playwright.sync_api import sync_playwright, expect
    from openprogram.webui.routes import applications
    from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState
    monkeypatch.setenv('HOME', str(tmp_path))
    source = tmp_path / 'software'
    source.mkdir()
    (source / 'application.json').write_text(json.dumps({
        'id': 'test.managed', 'title': 'Managed notes', 'version': '1', 'capabilities': ['storage.app'],
    }))
    (source / 'index.html').write_text('''<!doctype html><title>Notes</title><label>Note<input id="note" disabled></label><button id="save" disabled>Save</button><output></output><script>
let version=0;openprogramApp.load().then(s=>{version=s.version;note.value=s.value.note||'';note.disabled=false;save.disabled=false;});save.onclick=async()=>{const s=await openprogramApp.save({note:note.value},version);version=s.version;document.querySelector('output').textContent='Saved';};</script>''')
    bundle = tmp_path / 'manager.js'
    entry = '''import React,{useState} from 'react'; import {createRoot} from 'react-dom/client';
import {AppRouterContext} from 'next/dist/shared/lib/app-router-context.shared-runtime';
import {ApplicationsPage} from './components/applications/applications-page';
import {NewTabPage} from './components/center-tabs/new-tab-page';
import {ApplicationTabPane} from './components/center-tabs/application-tab-pane';
import {useCenterTabs} from './lib/state/center-tabs-store';
function App(){const[manage,setManage]=useState(true);const tab=useCenterTabs(s=>s.tabs.find(t=>t.id===s.activeId));return <AppRouterContext.Provider value={{push:()=>setManage(false)}}><button onClick={()=>setManage(true)}>Manage software</button><button onClick={()=>setManage(false)}>Launcher</button>{manage?<ApplicationsPage/>:<><NewTabPage/>{tab?.applicationInstanceId&&<ApplicationTabPane instanceId={tab.applicationInstanceId}/>}</>}</AppRouterContext.Provider>;}createRoot(document.getElementById('root')).render(<App/>);'''
    subprocess.run(['node', '-e', '''require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});''', str(ROOT / 'apps/web'), str(bundle), entry], cwd=ROOT, check=True, capture_output=True)
    app = FastAPI()
    applications.register(app)
    @app.get('/applications')
    def shell():
        return Response('<div id="root"></div><script src="/manager.js"></script>', media_type='text/html')
    @app.get('/manager.js')
    def script():
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
                context.add_cookies([{'name': auth.cookie_name, 'value': auth.cookie_value, 'url': f'http://127.0.0.1:{port}', 'httpOnly': True, 'sameSite': 'Strict'}])
                page = context.new_page()
                page.goto(f'http://127.0.0.1:{port}/applications')
                page.get_by_label('Application directory', exact=True).fill(str(source))
                page.get_by_role('button', name='Install application', exact=True).click()
                row = page.get_by_role('region', name='Managed notes')
                expect(row).to_be_visible()
                row.get_by_role('button', name='Open', exact=True).click()
                frame = page.frame_locator('iframe')
                frame.get_by_label('Note', exact=True).fill('retained after uninstall')
                frame.get_by_role('button', name='Save', exact=True).click()
                expect(frame.locator('output')).to_have_text('Saved')
                page.get_by_role('button', name='Manage software', exact=True).click()
                row.get_by_label('Enabled', exact=True).click()
                expect(row.get_by_label('Enabled', exact=True)).not_to_be_checked()
                expect(row.get_by_role('button', name='Open', exact=True)).to_be_disabled()
                page.get_by_role('button', name='Launcher', exact=True).click()
                expect(page.get_by_role('button', name='Managed notes', exact=True)).to_have_count(0)
                page.get_by_role('button', name='Manage software', exact=True).click()
                row.get_by_label('Enabled', exact=True).click()
                expect(row.get_by_label('Enabled', exact=True)).to_be_checked()
                row.get_by_role('button', name='Uninstall', exact=True).click()
                expect(row).to_have_count(0)
                page.get_by_label('Application directory', exact=True).fill(str(source))
                page.get_by_role('button', name='Install application', exact=True).click()
                row.get_by_role('button', name='Open', exact=True).click()
                expect(page.frame_locator('iframe').get_by_label('Note', exact=True)).to_have_value('retained after uninstall')
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
