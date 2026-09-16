"""Render the real submit and scroll hooks together; fake only the backend.

Unlike notifier-only tests, these cases click Send while a task is running
and measure the resulting DOM scroll position in Chromium. This is a focused
React integration harness, not a full desktop App or backend acceptance test.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser

ENTRY = r'''
import React, {useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {useChatSubmit} from './components/chat/composer/submit/use-chat-submit';
import {useChatAreaStick} from './components/chat/messages/use-chat-area-stick';
import {useSessionStore} from './lib/session-store';
import {useSendQueue, queueFor} from './lib/chat/send-queue';
import {peekTakeLatest} from './lib/chat/chat-scroll';
import {runtimeState, setSocket} from './lib/runtime-bridge/state';

const EMPTY = [];
window.framesSent = [];
window.steerCommands = [];
window.finishedSubmits = 0;
window.submitErrors = [];
class Socket extends EventTarget {
  static OPEN = 1;
  readyState = 1;
  send(wire) { window.framesSent.push(JSON.parse(wire)); }
}
window.WebSocket = Socket;
setSocket(new Socket());
// Hold the steer receipt until Python releases it: following must happen on
// accepted local submission, not only after a server acknowledgement arrives.
window.fetch = async (url, init = {}) => {
  const path = String(url);
  if (path === '/api/execution/steer' && init.method === 'POST') {
    const command = JSON.parse(init.body);
    window.steerCommands.push(command);
    return new Promise(resolve => {
      window.ackSteer = () => resolve(Response.json({command: {
        command_id: command.command_id, status: 'applied',
      }}));
    });
  }
  const match = /^\/api\/execution\/exec-(main|peer)(?:\?|$)/.exec(path);
  if (match) return Response.json({snapshot: {
    session_id: match[1], execution_id: `exec-${match[1]}`,
    status_version: 7, status: 'running', capabilities: {steer: true},
  }});
  throw new Error(`Unexpected request: ${path}`);
};
const messagesById = {}, messageOrder = {}, runningTasks = {};
for (const sid of ['main', 'peer']) {
  messageOrder[sid] = Array.from({length: 40}, (_, i) => `${sid}-${i}`);
  for (const id of messageOrder[sid]) {
    messagesById[id] = {id, role: 'user', content: id, status: 'done'};
  }
  runningTasks[sid] = {
    session_id: sid, msg_id: `${sid}-39`, execution_id: `exec-${sid}`,
    status_version: 7,
  };
}
useSessionStore.setState({
  currentSessionId: 'main', activeChatKey: 'main', welcomeVisible: false,
  messagesById, messageOrder, runningTasks, composerDrafts: {},
});
runtimeState.currentSessionId = 'main';
window.readQueue = sid => queueFor(sid);
window.readNote = sid => peekTakeLatest(sid, sid === 'peer' ? 'peer:peer' : 'main');
window.grow = sid => useSessionStore.getState().appendMessage(sid, {
  id: `${sid}-delayed-growth`, role: 'assistant', content: 'delayed growth', status: 'done',
});

function Pane({sid, peer = false}) {
  const areaRef = useRef(null), columnRef = useRef(null);
  const ids = useSessionStore(s => s.messageOrder[sid] ?? EMPTY);
  const input = useSessionStore(s => s.composerDrafts[sid] ?? '');
  const running = useSessionStore(s => !!s.runningTasks[sid]);
  const queued = useSendQueue(s => s.queues[sid] ?? EMPTY);
  const [mode, setMode] = useState('queue');
  const [attachment, setAttachment] = useState(false);
  const {detached, jumpToLatest} = useChatAreaStick(
    peer ? `peer:${sid}` : sid, ids.at(-1) ?? null, true,
    peer ? {sessionId: sid, areaRef, columnRef} : undefined,
  );
  const setInput = (owner, value) => useSessionStore.setState(s => ({
    composerDrafts: {...s.composerDrafts, [owner]: value},
  }));
  const {submit} = useChatSubmit({
    bound: peer ? sid : null, activeChatKey: sid, currentSessionId: sid,
    input, isRunning: running, noEnabledModels: false, promptNeedModel() {},
    send: () => true, setComposerInputFor: setInput, setHistoryIndex() {},
    slash: {runCommand: () => false, close() {}}, pendingImages: [],
    pendingDocs: attachment ? [{id: 'attached', filename: 'note.txt'}] : [],
    clearAttachmentsAfterSubmit() {}, thinking: 'medium', toolsEnabled: true,
    toolsProfile: '__agent__', webSearchEnabled: false, fastEnabled: false,
    fastSupported: false, runningMessageMode: mode, dispatchFunction: () => false,
  });
  return <section className="pane">
    <div id={peer ? undefined : 'chatArea'} ref={areaRef} className="area"
      data-testid={`${sid}-area`} tabIndex={0}>
      <div id={peer ? undefined : 'chatMessages'} ref={columnRef} className="messages">
        {ids.map(id => <div className="row" data-msg-id={id} key={id}>{id}</div>)}
        {queued.map(item => <div className="row" data-queued-id={item.id} key={item.id}>{item.text}</div>)}
      </div>
    </div>
    {detached && <button onClick={jumpToLatest}>Jump {sid}</button>}
    <input aria-label={`Message ${sid}`} value={input}
      onChange={event => setInput(sid, event.target.value)} />
    <select aria-label={`Mode ${sid}`} value={mode} onChange={event => setMode(event.target.value)}>
      <option value="queue">Queue</option><option value="steer">Steer</option>
    </select>
    <label><input type="checkbox" checked={attachment}
      onChange={event => setAttachment(event.target.checked)} />Attachment {sid}</label>
    <button onClick={() => void submit().then(
      () => { window.finishedSubmits += 1; },
      error => { window.submitErrors.push(String(error)); },
    )}>Send {sid}</button>
  </section>;
}
createRoot(document.getElementById('mount')).render(
  <React.StrictMode><Pane sid="main" /><Pane sid="peer" peer /></React.StrictMode>,
);
'''


@pytest.fixture(scope="module")
def submit_follow_bundle(tmp_path_factory):
    directory = tmp_path_factory.mktemp("submit-follow")
    bundle = directory / "submit-follow.js"
    subprocess.run(
        [
            "node", "-e",
            "require('esbuild').buildSync({stdin:{contents:process.argv[3],resolveDir:process.argv[1],loader:'tsx'},bundle:true,format:'iife',platform:'browser',jsx:'automatic',loader:{'.css':'empty'},outfile:process.argv[2],tsconfig:process.argv[1]+'/tsconfig.json'});",
            str(ROOT / "apps/web"), str(bundle), ENTRY,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    shell = directory / "submit-follow.html"
    shell.write_text(
        "<!doctype html><style>"
        "#mount{display:flex;gap:16px}.pane{width:45%;min-width:0}"
        ".area{height:500px;overflow:auto;overflow-anchor:none}"
        ".row{height:120px}.messages{padding-bottom:120px}"
        "</style><div id='mount'></div>",
        encoding="utf-8",
    )
    return shell, bundle


@pytest.mark.parametrize("sid", ["main", "peer"])
@pytest.mark.parametrize("mode", ["queue", "steer"])
def test_running_submit_follows_without_moving_other_pane(submit_follow_bundle, sid, mode):
    from playwright.sync_api import expect, sync_playwright

    shell, bundle = submit_follow_bundle
    other = "peer" if sid == "main" else "main"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 850})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(shell.as_uri())
            page.add_script_tag(path=str(bundle))
            area = page.get_by_test_id(f"{sid}-area")
            other_area = page.get_by_test_id(f"{other}-area")
            expect(page.get_by_role("button", name=f"Send {sid}", exact=True)).to_be_visible()
            page.wait_for_function("[...document.querySelectorAll('.area')].every(a => a.scrollTop > 100)")
            page.evaluate("document.querySelectorAll('.area').forEach(a => {a.scrollTop=0;a.dispatchEvent(new Event('scroll'))})")
            expect(page.get_by_role("button", name=f"Jump {sid}", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name=f"Jump {other}", exact=True)).to_be_visible()
            other_top = other_area.evaluate("a => a.scrollTop")
            page.get_by_label(f"Mode {sid}").select_option(mode)
            message = page.get_by_label(f"Message {sid}", exact=True)
            send = page.get_by_role("button", name=f"Send {sid}", exact=True)

            # A rejected attachment must neither clear input nor move the view.
            page.get_by_label(f"Attachment {sid}", exact=True).check()
            message.fill("keep the attached draft")
            send.click()
            page.wait_for_function("window.finishedSubmits === 1")
            expect(message).to_have_value("keep the attached draft")
            assert page.evaluate("sid => window.readNote(sid)", sid) is None
            assert page.evaluate("sid => window.readQueue(sid).length", sid) == 0
            assert area.evaluate("a => a.scrollTop") < 2

            page.get_by_label(f"Attachment {sid}", exact=True).uncheck()
            message.fill("follow this accepted submission")
            send.click()
            page.wait_for_function("window.finishedSubmits === 2")
            expect(message).to_have_value("")
            page.wait_for_function(
                "sid => {const a=document.querySelector(`[data-testid='${sid}-area']`);return Math.abs(a.scrollHeight-a.clientHeight-a.scrollTop)<2}",
                arg=sid,
            )
            expect(page.get_by_role("button", name=f"Jump {sid}", exact=True)).to_have_count(0)
            assert other_area.evaluate("a => a.scrollTop") == other_top
            assert page.evaluate("window.framesSent.length") == 0
            if mode == "steer":
                page.wait_for_function("window.steerCommands.length === 1 && typeof window.ackSteer === 'function'")
                assert page.evaluate("sid => window.readQueue(sid).length", sid) == 1
                page.evaluate("window.ackSteer()")
                page.wait_for_function("sid => window.readQueue(sid).length === 0", arg=sid)
            else:
                assert page.evaluate("sid => window.readQueue(sid)[0].text", sid) == "follow this accepted submission"

            # Real wheel input detaches again; delayed growth must not resnap.
            area.hover()
            page.mouse.wheel(0, -10000)
            page.wait_for_function(
                "sid => document.querySelector(`[data-testid='${sid}-area']`).scrollTop < 2",
                arg=sid,
            )
            expect(page.get_by_role("button", name=f"Jump {sid}", exact=True)).to_be_visible()
            page.evaluate("sid => window.grow(sid)", sid)
            # Outlast the hook's 600 ms pointer-growth suppression window.
            page.wait_for_timeout(750)
            assert area.evaluate("a => a.scrollTop") < 2
            assert other_area.evaluate("a => a.scrollTop") == other_top
            assert page.evaluate("window.submitErrors") == []
            assert errors == []
        finally:
            browser.close()
