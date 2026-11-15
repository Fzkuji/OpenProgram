// The message action row: a user message has Copy, Edit message, Rewind to
// here and Fork from here; a reply has Copy, Regenerate and Pin as chapter (which pins the
// turn above it, on that turn's user message). Neither has Quote (selecting
// text quotes) or Branch (copying the conversation is in its ⋯ menu). The
// `< N/M >` version arrows switch versions through /api/chat/checkout.
import assert from 'node:assert/strict';
import test from 'node:test';
import {join, dirname} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {build} from 'esbuild';
import {parseHTML} from 'linkedom';

import {scratchDir} from '../../scratch-dir.mjs';

const web = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = scratchDir(web, 'message-action-row-test');
const stubs = {
  session: `const st=()=>globalThis.__row.store;
export const selectRow=(view,id)=>(state)=>state.rowsByView[view]?.[id];
export const selectViewRows=(view)=>(state)=>state.rowsByView[view]??{};
    export const useSessionStore=Object.assign(sel=>sel(st()),{getState:st});`,
  i18n: 'export const useTranslation=()=>({text:en=>en}); export const translateText=en=>en;',
  toast: 'export const showToast=(m,o)=>globalThis.__row.toasts.push({m,o});',
  state: 'export const getSocket=()=>null; export const runtimeState=globalThis.__row.runtime;',
  load: 'export const sendRuntimeCommand=p=>{globalThis.__row.sent.push(p);return true;};',
  handlers: 'export const setRunActive=()=>{};',
  clipboard: 'export const copyText=async t=>{globalThis.__row.copied.push(t);return true;};',
  tooltip: 'export const HoverTip=({children})=>children; export const TipBody=()=>null;',
  icons: 'export const CopyIcon=()=>null,CheckIcon=()=>null,RefreshCwIcon=()=>null,SquarePenIcon=()=>null,UndoIcon=()=>null,BookmarkIcon=()=>null;',
  wsRequest: `export const wsRequest=async(action,payload,type,opts)=>{const r=globalThis.__row;
    r.requests.push({action,payload,type,opts}); return r.reply(action,payload);};`,
  rewindActions: `export const requestRewind=(...a)=>globalThis.__row.rewinds.push(a);
    export const forkFromMessage=(s,id)=>{const r=globalThis.__row;r.forks.push([s,r.store.messageOrder.S?.at(-1)??id]);};`,
  anim: 'export const useActionIconAnimation=()=>({ref:null,handlers:{}});',
};
const routes = [
  [/\/session-store$/, 'session'], [/\/i18n$/, 'i18n'], [/format-utils\/toast$/, 'toast'],
  [/runtime-bridge\/state$/, 'state'], [/session\/session-load$/, 'load'],
  [/session\/chat-handlers$/, 'handlers'], [/lib\/clipboard$/, 'clipboard'],
  [/ui\/tooltip$/, 'tooltip'], [/animated-icons$/, 'icons'], [/use-action-icon-animation$/, 'anim'],
  [/net\/ws\/ws-request$/, 'wsRequest'],
  [/rewind\/rewind-actions$/, 'rewindActions'],
];
await build({
  absWorkingDir: web,
  stdin: {contents: `export { MessageActions } from './components/chat/messages/bubbles/message-actions';`, resolveDir: web},
  bundle: true, format: 'esm', platform: 'node', packages: 'external',
  outfile: join(dir, 'test.mjs'), jsx: 'automatic', tsconfig: join(web, 'tsconfig.json'),
  plugins: [{name: 'stubs', setup(b) {
    b.onResolve({filter: /^react$/}, () => ({path: 'react', external: true}));
    for (const [filter, name] of routes) b.onResolve({filter}, () => ({path: name, namespace: 'stub'}));
    b.onLoad({filter: /.*/, namespace: 'stub'}, ({path}) => ({contents: stubs[path], resolveDir: web}));
  }}],
});

const {window} = parseHTML('<html><body></body></html>');
Object.assign(globalThis, {window, document: window.document, HTMLElement: window.HTMLElement,
  Element: window.Element, IS_REACT_ACT_ENVIRONMENT: true});
const row = globalThis.__row = {toasts: [], sent: [], copied: [], fetches: [], requests: [], reply: null, runtime: {_postCheckoutScrollTo: null}, store: null};
const {createElement: h, act} = await import('react');
const {createRoot} = await import('react-dom/client');
const {MessageActions} = await import(pathToFileURL(join(dir, 'test.mjs')));

function reset() {
  Object.assign(row, {toasts: [], sent: [], copied: [], fetches: [], requests: [], composer: [], answers: {}, rewinds: [], forks: []});
  row.reply = (action, payload) => ({session_id: payload.session_id, message_id: payload.message_id,
    chapter: payload.pinned ? {pinned_at: 1_700_000_100} : null, action});
  row.runtime._postCheckoutScrollTo = null;
  row.local = [];
  row.store = {currentSessionId: 'S', runningTasks: {}, rowsByView: {}, messageOrder: {},
    setComposerInputFor: (key, text) => row.composer.push([key, text]),
    applyLocalChange(view, change) { row.local.push([view, change]); }};
  globalThis.fetch = async (url, init) => {
    row.fetches.push({url, body: JSON.parse(init.body)});
    return {ok: true, status: 200, json: async () => row.answers[url] ?? ({})};
  };
}

async function mount(props) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(() => root.render(h(MessageActions, props)));
  const button = (name) => host.querySelector(`button[aria-label="${name}"]`);
  return {
    host, button,
    async click(name) {
      const el = button(name);
      assert.ok(el, `button ${name}`);
      await act(async () => el.dispatchEvent(new window.Event('click', {bubbles: true})));
      await act(async () => { for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0)); });
    },
    async close() { await act(() => root.unmount()); host.remove(); },
  };
}

const reply = {id: 'r1', role: 'assistant', content: 'first answer', timestamp: 1_700_000_000};
const user = {id: 'u1', role: 'user', content: 'question', timestamp: 1_700_000_000};

test('the timestamp stays on a user message', async () => {
  reset();
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    assert.ok(view.host.querySelector('.message-timestamp'), 'the timestamp stays');
  } finally { await view.close(); }
});

test('a reply has Copy and Regenerate, and Pin as chapter once its turn is known', async () => {
  reset();
  const view = await mount({msg: reply});
  try {
    const all = [...view.host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));
    assert.deepEqual(all, ['Copy', 'Regenerate']);
  } finally { await view.close(); }
  reset();
  seedTurn();
  const turn = await mount({msg: reply});
  try {
    const all = [...turn.host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));
    assert.deepEqual(all, ['Copy', 'Regenerate', 'Pin as chapter']);
  } finally { await turn.close(); }
});

function seedTurn(pinnedAt) {
  row.store.rowsByView = {S: {
    u1: {...user, ...(pinnedAt ? {chapterPinnedAt: pinnedAt} : {})},
    r1: reply,
  }};
  row.store.messageOrder = {S: ['u1', 'r1']};
}

test('Pin as chapter pins the user message above the reply over the WS', async () => {
  reset();
  seedTurn();
  const view = await mount({msg: reply});
  try {
    const button = view.button('Pin as chapter');
    assert.equal(button.getAttribute('aria-pressed'), 'false');
    assert.equal(button.classList.contains('is-chapter'), false);
    assert.equal(button.closest('.message-actions').classList.contains('is-chapter-pinned'), false);
    await view.click('Pin as chapter');
    assert.deepEqual(row.requests.map(({action, payload, type, opts}) => ({action, payload, type, opts})), [{
      action: 'set_message_chapter',
      payload: {session_id: 'S', message_id: 'u1', pinned: true},
      type: 'message_chapter_updated',
      opts: {requestId: true},
    }]);
    // The pin shows at once over the row; the server's row update carries its time.
    const [shownIn, shown] = row.local[0];
    assert.equal(shownIn, 'S');
    assert.equal(shown.kind, 'overlay_set');
    assert.equal(shown.rowId, 'u1');
    assert.ok(shown.fields.chapterPinnedAt > 0);
    assert.deepEqual(row.local[1], ['S', {kind: 'overlay_clear', rowId: 'u1'}]);
    assert.equal(row.toasts.length, 0);
  } finally { await view.close(); }
});

test('a pinned turn shows Unpin chapter in the chapter colour and unpins', async () => {
  reset();
  seedTurn(1_700_000_000);
  const view = await mount({msg: reply});
  try {
    const button = view.button('Unpin chapter');
    assert.ok(button, 'the pinned label');
    assert.equal(button.getAttribute('aria-pressed'), 'true');
    assert.equal(button.classList.contains('is-chapter'), true);
    // The pinned reply's row stays shown, not only on hover.
    assert.equal(button.closest('.message-actions').classList.contains('is-chapter-pinned'), true);
    await view.click('Unpin chapter');
    assert.equal(row.requests[0].payload.pinned, false);
    assert.equal(row.local[0][1].kind, 'overlay_set');
    assert.equal(row.local[0][1].fields.chapterPinnedAt, undefined);
  } finally { await view.close(); }
});

test('a refused pin goes back and says so', async () => {
  reset();
  seedTurn();
  row.reply = () => ({status: 'error', code: 'message_not_found'});
  const view = await mount({msg: reply});
  try {
    await view.click('Pin as chapter');
    assert.deepEqual(row.local.at(-1), ['S', {kind: 'overlay_clear', rowId: 'u1'}]);
    assert.equal(row.toasts.length, 1);
    assert.equal(row.toasts[0].o.tone, 'error');
  } finally { await view.close(); }
});

test('Regenerate reruns the turn above the reply', async () => {
  reset();
  const view = await mount({msg: reply});
  try {
    await view.click('Regenerate');
    assert.deepEqual(row.fetches, [{url: '/api/chat/retry', body: {session_id: 'S', msg_id: 'r1'}}]);
    assert.deepEqual(row.sent, [{action: 'load_session', session_id: 'S'}]);
  } finally { await view.close(); }
});

test('Copy copies the message text', async () => {
  reset();
  const view = await mount({msg: reply});
  try {
    await view.click('Copy');
    assert.deepEqual(row.copied, ['first answer']);
  } finally { await view.close(); }
});

test('the version arrows switch versions in place', async () => {
  reset();
  const view = await mount({msg: {...user, id: 'u3', siblingIndex: 2, siblingTotal: 2, prevSiblingId: 'r2', nextSiblingId: null}, onEdit: () => {}});
  try {
    assert.match(view.host.querySelector('.message-nav-label').textContent, /2 \/ 2/);
    assert.equal(view.button('Next version').disabled, true);
    await view.click('Previous version');
    assert.deepEqual(row.fetches, [{url: '/api/chat/checkout', body: {session_id: 'S', msg_id: 'r2'}}]);
    assert.equal(row.runtime._postCheckoutScrollTo, 'r2');
  } finally { await view.close(); }
});

test('a user message offers Rewind and Fork after Edit whatever its replies did', async () => {
  reset();
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    const all = [...view.host.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'));
    assert.deepEqual(all, ['Copy', 'Edit message', 'Rewind to here', 'Fork from here']);
    assert.match(view.button('Rewind to here').getAttribute('aria-description'), /out of the conversation/);
  } finally { await view.close(); }
});

test('a message still being sent cannot rewind', async () => {
  reset();
  const view = await mount({msg: {...user, status: 'pending'}, onEdit: () => {}});
  try {
    assert.equal(view.button('Rewind to here'), null);
  } finally { await view.close(); }
});

test('Rewind is disabled with its reason while a turn runs', async () => {
  reset();
  row.store.runningTasks = {S: {}};
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    assert.equal(view.button('Rewind to here').disabled, true);
    await view.click('Rewind to here');
    assert.equal(row.fetches.length, 0);
  } finally { await view.close(); }
});

test('Fork is hidden while the message is still being answered', async () => {
  reset();
  row.store.runningTasks = {S: {}};
  row.store.rowsByView = {S: {u1: user}};
  row.store.messageOrder = {S: ['u1']};
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    assert.equal(view.button('Fork from here'), null);
    assert.ok(view.button('Rewind to here'));
  } finally { await view.close(); }
});

test('an archived conversation offers Fork but not Rewind', async () => {
  reset();
  row.store.conversations = {S: {archived: true}};
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    assert.equal(view.button('Rewind to here'), null);
    assert.ok(view.button('Fork from here'));
  } finally { await view.close(); row.store.conversations = {}; }
});

test('Fork copies through the reply of the message', async () => {
  reset();
  row.store.rowsByView = {S: {u1: user, r1: reply}};
  row.store.messageOrder = {S: ['u1', 'r1']};
  row.answers['/api/chat/branch_session'] = {ok: true, new_session_id: 'S2'};
  const view = await mount({msg: user, onEdit: () => {}});
  try {
    await view.click('Fork from here');
    assert.deepEqual(row.forks, [['S', 'r1']]);
  } finally { await view.close(); }
});
