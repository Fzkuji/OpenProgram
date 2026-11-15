// Opening and closing a rail never mounts, unmounts or re-creates anything
// inside it, in either direction or when the user toggles again mid-move.
// Only the shell's width moves, from the click's frame, and `.collapsed`
// comes and goes in that same render. What only the open rail shows stays
// mounted at the open width and fades (opacity alone, no inherited
// property changes on a move), and is inert from a close's settle to an
// open's settle; what both states show keeps its element.
// `data-rail-closed` marks a close that has settled; nothing visible keys
// on it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';
import postcss from 'postcss';

import { scratchDir } from '../scratch-dir.mjs';

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(web, 'rail-open-close');
const SETTLE_MS = 320;

const harness = `
import { createElement } from "react";
import { useResizableRail } from "./components/layout/use-resizable-rail";
export const handles = {};
export const renders = { count: 0 };
export function Rail({ open, direction = 1 }) {
  renders.count += 1;
  const { style, shellProps, closedSettled } = useResizableRail({ open, minWidth: 200, maxWidth: 480, defaultWidth: 320, direction });
  handles.end = shellProps.onTransitionEnd;
  return createElement("div", { id: "rail", style, ...shellProps, "data-settled": String(closedSettled) },
    createElement("p", { className: "rail-open-only" }, createElement("span", { className: "rail-open-only" })),
    createElement("b", { className: "rail-row" }));
}`;

const sidebarStubs = {
  'next/navigation': 'export const usePathname=()=>"/s/a";',
  '@/lib/i18n': 'const tr={t:x=>x,text:(en)=>en}; export const useTranslation=()=>tr;',
  '../animated-icons': 'import React,{forwardRef} from "react"; const Icon=forwardRef(({className},ref)=>React.createElement("i",{className})); export const PanelLeftCloseIcon=Icon,PanelLeftOpenIcon=Icon,PlusIcon=Icon;',
  'framer-motion': 'import React,{forwardRef} from "react"; export const motion={div:forwardRef(({layoutScroll,...props},ref)=>React.createElement("div",{...props,ref}))};',
  '../user-menu-footer': 'import React from "react"; export const UserMenuFooter=()=>React.createElement("div",{className:"user-menu-footer"});',
  './sessions/sessions-list': 'import React from "react"; export const SessionsList=()=>React.createElement("output",{"data-sessions":""});',
  './sessions/favorites-list': 'export const FavoritesList=()=>null;',
  './sessions/section-header': 'export const SectionHeader=()=>null;',
  './sidebar-primary-nav': 'import React from "react"; export const SidebarPrimaryNav=()=>React.createElement("nav",{"data-links":""});',
  './use-window-globals': 'const g={availableFunctions:[{name:"f"}],programsMeta:{favorites:["f"]}}; export const useWindowGlobals=()=>g;',
  '@/lib/runtime-bridge/state': 'export const runtimeState={};',
  '@/lib/tabs/navigation/open-session': 'export const openNewChat=()=>"draft";',
  './use-stable-scroll-end': 'export const useStableScrollEnd=()=>{};',
};
const rightStubs = {
  'next/navigation': 'export const usePathname=()=>"/s/a";',
  '@/lib/i18n': 'const tr={t:x=>x,text:(en)=>en}; export const useTranslation=()=>tr;',
  '@/lib/net/ws/ws-request': 'export const wsRequest=()=>new Promise(()=>{});',
  '../session-resources/session-resources-panel': 'import React from "react"; export const SessionResourcesPanel=({active})=>React.createElement("output",{"data-resources":"","data-active":String(active)});',
  '../../net/fetch-client.ts': 'export const jsonFetch=async()=>({items:[]});',
  './context-commit-timeline': 'export const ContextCommitTimeline=()=>null;',
  '../animated-icons': 'import React,{forwardRef} from "react"; const Icon=forwardRef(({className},ref)=>React.createElement("i",{className})); export const ActivityIcon=Icon,BoxIcon=Icon,FolderOpenIcon=Icon,PanelLeftCloseIcon=Icon,PanelLeftOpenIcon=Icon;',
  '../files/tree/file-tree': 'export const FileTree=()=>null;',
  './running/running-panel': 'import React from "react"; export const RunningPanel=({active})=>React.createElement("output",{"data-running-active":String(active)});',
  './detail-panel': 'export const DetailPanel=()=>null,SessionViewSwitch=()=>null,VIEW_CONTEXT="context",VIEW_DETAIL="detail";',
};
const fixtures = (stubs) => ({ name: 'fixtures', setup(b) {
  b.onResolve({ filter: /.*/ }, (a) => (a.path in stubs ? { path: a.path, namespace: 'fixture' } : null));
  b.onLoad({ filter: /.*/, namespace: 'fixture' }, (a) => ({ contents: stubs[a.path], resolveDir: web }));
} });
const common = { absWorkingDir: web, bundle: true, format: 'esm', platform: 'node', packages: 'external', jsx: 'automatic', tsconfig: join(web, 'tsconfig.json'), loader: { '.css': 'empty' } };

let Rail; let handles; let renders; let Sidebar; let RightSidebar; let useSessionStore;
let act; let createElement; let createRoot;
const frames = [];
const timers = new Map();
let nextId = 1;

test.before(async () => {
  await build({ ...common, stdin: { contents: harness, resolveDir: web, loader: 'jsx' }, outfile: join(dir, 'hook.mjs') });
  await build({ ...common, stdin: { contents: "export {Sidebar} from './components/sidebar/sidebar';", resolveDir: web }, outfile: join(dir, 'sidebar.mjs'), plugins: [fixtures(sidebarStubs)] });
  await build({ ...common, stdin: { contents: "export {RightSidebar} from './components/right-sidebar/right-sidebar'; export {useSessionStore} from './lib/session-store';", resolveDir: web }, outfile: join(dir, 'right.mjs'), plugins: [fixtures(rightStubs)] });
  const { window } = parseHTML('<html><body></body></html>');
  window.requestAnimationFrame = (fn) => { const id = nextId++; frames.push({ id, fn }); return id; };
  window.cancelAnimationFrame = (id) => { const i = frames.findIndex((f) => f.id === id); if (i >= 0) frames.splice(i, 1); };
  window.setTimeout = (fn, ms) => { const id = nextId++; timers.set(id, { fn, ms }); return id; };
  window.clearTimeout = (id) => { timers.delete(id); };
  window.location = { pathname: '/s/a' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  ({ act, createElement } = await import('react'));
  ({ createRoot } = await import('react-dom/client'));
  ({ Rail, handles, renders } = await import(pathToFileURL(join(dir, 'hook.mjs'))));
  ({ Sidebar } = await import(pathToFileURL(join(dir, 'sidebar.mjs'))));
  ({ RightSidebar, useSessionStore } = await import(pathToFileURL(join(dir, 'right.mjs'))));
});

async function frame() {
  const due = frames.splice(0);
  await act(async () => { for (const f of due) f.fn(0); });
}
async function timeout(ms) {
  const due = [...timers].filter(([, t]) => t.ms === ms);
  await act(async () => { for (const [id, t] of due) { timers.delete(id); t.fn(); } });
}
async function mount(element) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(element));
  // The rail's first frame: a close before it is the mount restoring a
  // stored closed rail, not a move.
  await frame();
  return {
    host,
    render: (next) => act(async () => root.render(next)),
    done: async () => { await act(async () => root.unmount()); host.remove(); frames.length = 0; timers.clear(); },
  };
}
const click = (el) => act(async () => { el.dispatchEvent(new window.Event('click', { bubbles: true })); });

test('the width moves from the click in both directions; only a settled close is marked', async () => {
  const view = await mount(createElement(Rail, { open: true }));
  const rail = view.host.querySelector('#rail');
  try {
    assert.equal(rail.style.getPropertyValue('--rail-open-w'), '320px');
    await view.render(createElement(Rail, { open: false }));
    assert.equal(rail.style.width, '62px', 'closing starts in the click frame');
    assert.equal(rail.style.getPropertyValue('--rail-open-w'), '320px', 'the open width stays');
    for (const name of ['data-rail-moving', 'data-rail-holding', 'data-rail-closing']) assert.equal(rail.hasAttribute(name), false, name);
    assert.equal(rail.hasAttribute('data-rail-closed'), false, 'not settled while moving');
    // Some other property ending, or a child's width, does not settle it.
    await act(async () => handles.end({ target: rail, currentTarget: rail, propertyName: 'min-width' }));
    await act(async () => handles.end({ target: rail.parentNode, currentTarget: rail, propertyName: 'width' }));
    assert.equal(rail.hasAttribute('data-rail-closed'), false);
    await act(async () => handles.end({ target: rail, currentTarget: rail, propertyName: 'width' }));
    assert.equal(rail.getAttribute('data-rail-closed'), '', 'the width arriving settles the close');
    const before = renders.count;
    await view.render(createElement(Rail, { open: true }));
    // (A render-phase update may run the function twice; one commit.)
    assert.ok(renders.count - before <= 2, 'opening renders the rail once');
    assert.equal(rail.style.width, '320px', 'opening starts in the click frame, without waiting');
    assert.equal(rail.style.minWidth, '200px');
    assert.equal(rail.hasAttribute('data-rail-closed'), false, 'the mark goes in the opening render');
    await frame(); await frame(); await timeout(100); await timeout(SETTLE_MS);
    assert.equal(rail.style.width, '320px');
    assert.equal(rail.hasAttribute('data-rail-closed'), false);
  } finally { await view.done(); }
});

test('a close whose width never reports its end settles on the timeout; a reopen before it never settles', async () => {
  const view = await mount(createElement(Rail, { open: true, direction: -1 }));
  const rail = view.host.querySelector('#rail');
  try {
    await view.render(createElement(Rail, { open: false, direction: -1 }));
    await view.render(createElement(Rail, { open: true, direction: -1 }));
    assert.equal(rail.style.width, '320px', 'reopening mid-close turns the width back at once');
    await timeout(SETTLE_MS);
    assert.equal(rail.hasAttribute('data-rail-closed'), false, 'the reopen cancelled the settle');
    await view.render(createElement(Rail, { open: false, direction: -1 }));
    await frame(); await frame();
    assert.equal(rail.hasAttribute('data-rail-closed'), false);
    await timeout(SETTLE_MS);
    assert.equal(rail.getAttribute('data-rail-closed'), '');
    assert.equal(timers.size, 0, 'nothing left pending');
  } finally { await view.done(); }
});

test('a close before the first frame (a stored closed rail on mount) is settled at once', async () => {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Rail, { open: true })));
    await act(async () => root.render(createElement(Rail, { open: false })));
    const rail = host.querySelector('#rail');
    assert.equal(rail.style.width, '62px');
    assert.equal(rail.getAttribute('data-rail-closed'), '');
  } finally { await act(async () => root.unmount()); host.remove(); frames.length = 0; timers.clear(); }
});

test('open-only parts become inert at a close\'s settle and reachable at an open\'s settle, never in the click', async () => {
  const view = await mount(createElement(Rail, { open: true }));
  const rail = view.host.querySelector('#rail');
  const outer = rail.querySelector('p');
  const inner = rail.querySelector('span');
  const end = () => act(async () => handles.end({ target: rail, currentTarget: rail, propertyName: 'width' }));
  try {
    assert.equal(outer.hasAttribute('inert'), false);
    await view.render(createElement(Rail, { open: false }));
    assert.equal(outer.hasAttribute('inert'), false, 'closing: not inert while the move runs');
    await end();
    assert.equal(outer.getAttribute('inert'), '', 'the close\'s settle makes it inert');
    assert.equal(inner.hasAttribute('inert'), false, 'an inner part is inert with its outer one');
    assert.equal(rail.querySelector('b').hasAttribute('inert'), false, 'what both states show stays reachable');
    await view.render(createElement(Rail, { open: true }));
    assert.equal(outer.getAttribute('inert'), '', 'opening: still inert while the move runs');
    await end();
    assert.equal(outer.hasAttribute('inert'), false, 'the open\'s settle makes it reachable');
    // The timers stand in for a width that never reports its end.
    await view.render(createElement(Rail, { open: false }));
    await timeout(SETTLE_MS);
    assert.equal(outer.getAttribute('inert'), '');
    await view.render(createElement(Rail, { open: true }));
    await frame();
    assert.equal(outer.getAttribute('inert'), '');
    await timeout(SETTLE_MS);
    assert.equal(outer.hasAttribute('inert'), false);
    // A close reversed before it settles never makes it inert; an open
    // reversed before it settles leaves it inert.
    await view.render(createElement(Rail, { open: false }));
    await view.render(createElement(Rail, { open: true }));
    await timeout(SETTLE_MS);
    assert.equal(outer.hasAttribute('inert'), false);
    await view.render(createElement(Rail, { open: false }));
    await timeout(SETTLE_MS);
    await view.render(createElement(Rail, { open: true }));
    await view.render(createElement(Rail, { open: false }));
    assert.equal(outer.getAttribute('inert'), '');
    await timeout(SETTLE_MS);
    assert.equal(outer.getAttribute('inert'), '');
    assert.equal(timers.size, 0, 'nothing left pending');
  } finally { await view.done(); }
});

test('a rail mounted closed starts inert', async () => {
  const view = await mount(createElement(Rail, { open: false }));
  try {
    assert.equal(view.host.querySelector('#rail p').getAttribute('inert'), '');
  } finally { await view.done(); }
});

/** Every element under `root`, so a test can tell that none was replaced
 *  (the resize handle included). */
const snapshot = (root) => new Set(root.querySelectorAll('*'));
function sameNodes(before, root, what) {
  const now = snapshot(root);
  const gone = [...before].filter((el) => !now.has(el)).map((el) => el.className || el.tagName);
  const added = [...now].filter((el) => !before.has(el)).map((el) => el.className || el.tagName);
  assert.deepEqual({ gone, added }, { gone: [], added: [] }, what);
}

test('the left sidebar keeps every element through a close, its settle, an open and a reopen mid-close', async () => {
  const view = await mount(createElement(Sidebar));
  const shell = () => view.host.querySelector('#sidebar');
  const toggle = view.host.querySelector('.sidebar-toggle');
  const all = () => snapshot(shell());
  try {
    const open = all();
    const parts = ['#convSection', '#favSection', '.rail-wordmark', '[data-sessions]', '[data-links]', '.rail-header', '#navNewChat .sidebar-nav-label', '.rail-toggle-close', '.rail-toggle-open'];
    for (const part of parts) assert.ok(shell().querySelector(part), part);
    await click(toggle);
    assert.equal(shell().style.width, '62px');
    assert.equal(shell().classList.contains('collapsed'), true, 'the collapsed look comes with the click, so the fades start there');
    assert.equal(toggle.getAttribute('aria-label'), 'Show sidebar');
    sameNodes(open, shell(), 'closing');
    const openOnly = ['#convSection', '#favSection', '.rail-wordmark'];
    for (const part of openOnly) assert.equal(shell().querySelector(part).hasAttribute('inert'), false, `${part} is not inert while the close moves`);
    for (const part of openOnly) assert.equal(shell().querySelector(part).hasAttribute('aria-hidden'), false, `${part}: inert alone takes it out of reach`);
    await timeout(SETTLE_MS);
    assert.equal(shell().getAttribute('data-rail-closed'), '');
    for (const part of openOnly) assert.equal(shell().querySelector(part).getAttribute('inert'), '', `${part} is inert at rest collapsed`);
    assert.equal(shell().querySelector('#navNewChat').hasAttribute('inert'), false, 'the rows stay reachable');
    sameNodes(open, shell(), 'at rest collapsed');
    assert.equal(view.host.querySelector('.sidebar-toggle'), toggle, 'one toggle element in both states');
    assert.equal(shell().querySelector('[class~="hidden"]'), null, 'nothing is display:none-d by a class');
    await click(toggle);
    assert.equal(shell().classList.contains('collapsed'), false);
    assert.equal(shell().style.width, '288px', 'opening starts in the click frame');
    assert.equal(shell().hasAttribute('data-rail-holding'), false);
    for (const part of openOnly) assert.equal(shell().querySelector(part).getAttribute('inert'), '', `${part} stays inert while the open moves`);
    sameNodes(open, shell(), 'opening');
    await timeout(SETTLE_MS);
    for (const part of openOnly) assert.equal(shell().querySelector(part).hasAttribute('inert'), false, `${part} is reachable once the open has settled`);
    // Closing, and opening again before the close has settled.
    await click(toggle);
    await frame();
    await click(toggle);
    assert.equal(shell().classList.contains('collapsed'), false);
    await timeout(SETTLE_MS);
    assert.equal(shell().hasAttribute('data-rail-closed'), false);
    sameNodes(open, shell(), 'reopened mid-close');
    // Closing mid-open.
    await click(toggle);
    await timeout(SETTLE_MS);
    await click(toggle);
    await click(toggle);
    assert.equal(shell().classList.contains('collapsed'), true);
    sameNodes(open, shell(), 'closed mid-open');
  } finally { await view.done(); }
});

test('the resize handle is one node in both states, first in the card, and inert while closed or closing', async () => {
  for (const [Component, id, setOpen] of [
    [Sidebar, '#sidebar', null],
    [RightSidebar, '#rightSidebar', (open) => useSessionStore.getState().setRightDockOpen(open)],
  ]) {
    if (setOpen) useSessionStore.setState({ currentSessionId: null, rightDock: { open: true, view: 'files' } });
    const view = await mount(createElement(Component));
    const shell = () => view.host.querySelector(id);
    const toggle = (open) => (setOpen ? act(async () => setOpen(open)) : click(shell().querySelector('.sidebar-toggle')));
    try {
      const handle = shell().querySelector('.rail-resize-handle');
      const card = shell().querySelector('[data-slot="sidebar-inner"]');
      const order = () => [...card.children].map((child) => child.className);
      const before = order();
      assert.ok(handle, id);
      assert.equal(handle.hasAttribute('data-off'), false, 'open: it takes the pointer');
      assert.equal(handle.hasAttribute('aria-hidden'), false);
      let captured = 0;
      handle.setPointerCapture = () => { captured += 1; };
      const press = () => act(async () => { handle.dispatchEvent(Object.assign(new window.Event('pointerdown', { bubbles: true }), { button: 0, pointerId: 1, clientX: 10 })); });
      await press();
      assert.equal(captured, 1, 'open: a press starts a drag');
      assert.equal(handle.getAttribute('data-resizing'), 'true');
      // Closing ends the drag.
      await toggle(false);
      assert.equal(handle.getAttribute('data-resizing'), 'false', 'a close ends a drag');
      assert.equal(shell().querySelector('.rail-resize-handle'), handle, `${id}: the same node while closing`);
      assert.equal(handle.getAttribute('data-off'), '', 'closing: no pointer');
      assert.equal(handle.getAttribute('aria-hidden'), 'true');
      assert.equal(handle.hasAttribute('tabindex'), false, 'never focusable');
      assert.deepEqual(order(), before, 'the card\'s children keep their order');
      await timeout(SETTLE_MS);
      assert.equal(shell().querySelector('.rail-resize-handle'), handle, `${id}: the same node at rest collapsed`);
      // A press on the closed rail's handle starts no drag.
      await press();
      assert.equal(captured, 1, 'closed: the handle ignores the press');
      assert.equal(handle.getAttribute('data-resizing'), 'false');
      await toggle(true);
      assert.equal(shell().querySelector('.rail-resize-handle'), handle);
      assert.equal(handle.hasAttribute('data-off'), false);
      assert.deepEqual(order(), before);
    } finally { await view.done(); }
  }
  const off = declarations('.rail-resize-handle[data-off]');
  assert.deepEqual(Object.fromEntries(off), { 'pointer-events': 'none' });
});

test('a scrolled list brings the links back on the close and resets its scroll only at the settle', async () => {
  const view = await mount(createElement(Sidebar));
  const toggle = view.host.querySelector('.sidebar-toggle');
  const scroller = view.host.querySelector('.rail-scroll');
  const links = scroller.firstElementChild;
  assert.ok(links.querySelector('[data-links]'));
  const calls = [];
  let animation;
  links.animate = (keyframes, options) => {
    animation = { playbackRate: 1, reversed: 0, cancelled: 0, reverse() { this.reversed += 1; this.playbackRate *= -1; }, cancel() { this.cancelled += 1; } };
    calls.push({ keyframes, options });
    return animation;
  };
  // Scrolled by 400px; the links (bottom 150px) are 250px above the top.
  scroller.scrollTop = 400;
  scroller.getBoundingClientRect = () => ({ top: 100, bottom: 700 });
  links.getBoundingClientRect = () => ({ top: -310, bottom: -150 });
  try {
    await click(toggle);
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].keyframes, [{ transform: 'translateY(250px)' }, { transform: 'translateY(400px)' }]);
    assert.equal(calls[0].options.duration, 200);
    assert.equal(calls[0].options.easing, 'cubic-bezier(0.4, 0, 0.2, 1)');
    assert.equal(scroller.scrollTop, 400, 'the scroll stays while the list fades');
    await click(toggle);
    assert.equal(animation.reversed, 1, 'reopening mid-close plays the slide back');
    await click(toggle);
    assert.equal(animation.reversed, 2, 'closing again plays it forward');
    assert.equal(calls.length, 1, 'one animation for the whole back and forth');
    await timeout(SETTLE_MS);
    assert.equal(animation.cancelled, 1, 'the slide is dropped at the settle');
    assert.equal(scroller.scrollTop, 0, 'in the same frame as the scroll goes back');
  } finally { await view.done(); }
});

test('the right panel keeps its rows, toggle, views and Resources mounted; only their work follows `open`', async () => {
  useSessionStore.setState({ currentSessionId: null, rightDock: { open: true, view: 'resources' } });
  const view = await mount(createElement(RightSidebar));
  const shell = () => view.host.querySelector('#rightSidebar');
  const all = () => snapshot(shell());
  try {
    const open = all();
    const host = shell().querySelector('.right-view-host');
    assert.ok(host.classList.contains('rail-open-only'));
    assert.equal(shell().querySelector('[data-resources]').dataset.active, 'true');
    const rows = [...shell().querySelectorAll('.right-nav-item')];
    assert.equal(rows.length, 3);
    for (const row of rows) assert.ok(row.classList.contains('rail-row'), 'each view button rides the card as one row');
    await act(async () => useSessionStore.getState().setRightDockOpen(false));
    assert.equal(shell().style.width, '62px');
    assert.equal(shell().classList.contains('collapsed'), true);
    assert.equal(host.hasAttribute('inert'), false, 'not inert while the close moves');
    assert.equal(shell().querySelector('[data-resources]').dataset.active, 'false', 'Resources stops working at the click, stays mounted');
    assert.equal(shell().querySelector('[data-running-active]').dataset.runningActive, 'false');
    sameNodes(open, shell(), 'closing');
    await timeout(SETTLE_MS);
    sameNodes(open, shell(), 'at rest collapsed');
    assert.equal(host.getAttribute('inert'), '', 'inert at rest collapsed');
    for (const row of rows) assert.equal(row.closest('[inert]'), null, 'the view buttons stay reachable');
    assert.deepEqual([...shell().querySelectorAll('.right-nav-item')], rows, 'the same row elements');
    await act(async () => useSessionStore.getState().setRightDockOpen(true));
    assert.equal(shell().style.width, '288px');
    assert.equal(host.getAttribute('inert'), '', 'still inert while the open moves');
    await timeout(SETTLE_MS);
    assert.equal(host.hasAttribute('inert'), false, 'reachable once the open has settled');
    assert.equal(shell().querySelector('[data-resources]').dataset.active, 'true');
    await act(async () => useSessionStore.getState().setRightDockOpen(false));
    await act(async () => useSessionStore.getState().setRightDockOpen(true));
    sameNodes(open, shell(), 'reopened mid-close');
  } finally { await view.done(); }
});

const read = (path) => postcss.parse(readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8'));
const sheets = ['app/styles/base/layout.css', 'app/styles/base/sidebar.css', 'app/styles/right-dock/view-host.css'].map(read);
function declarations(selector) {
  const found = new Map();
  for (const sheet of sheets) sheet.walkRules((rule) => { if (rule.selectors?.includes(selector)) rule.walkDecls((d) => { found.set(d.prop, d.value.replace(/\s+/g, ' ') + (d.important ? ' !important' : '')); }); });
  return found;
}

test('open-only parts fade by opacity alone, with no display switch', () => {
  const base = declarations('.rail-open-only');
  assert.equal(base.get('transition'), 'opacity var(--rail-move-duration) var(--rail-move-easing)');
  const collapsed = declarations('.rail-shell.collapsed :is(.rail-open-only, .rail-row > .sidebar-nav-label, .rail-row > .sidebar-nav-text)');
  assert.deepEqual(Object.fromEntries(collapsed), { opacity: '0' });
  assert.deepEqual(Object.fromEntries(declarations('.sidebar.collapsed .user-menu-footer-info')), { opacity: '0' });
  assert.deepEqual(Object.fromEntries(declarations('.sidebar.collapsed .user-menu-footer-chevron')), { opacity: '0' });
  // Laid out at the open width in both states, against the edge that stays put.
  assert.equal(declarations('.rail-shell').get('--rail-open-card-w'), 'calc(var(--rail-open-w) - 4 * var(--spacing))');
  assert.equal(declarations('.rail-open-width').get('width'), 'var(--rail-open-card-w)');
  assert.equal(declarations('.rail-open-width').get('align-self'), 'flex-start');
  assert.equal(declarations('.right-sidebar .rail-open-width').get('align-self'), 'flex-end');
  // A row's label keeps the open row's room: 288px rail → 288 - 16 - 62 = 210px.
  const label = declarations('.rail-shell .rail-row > :is(.sidebar-nav-label, .sidebar-nav-text)');
  assert.equal(label.get('flex'), 'none');
  assert.equal(label.get('width'), 'calc(var(--rail-open-card-w) - 2 * 8px - 2 * 1px - 2 * 8px - 16px - 12px)');
  assert.equal(declarations('.rail-shell .rail-row').get('overflow'), 'hidden');
  // Nothing in a rail switches display, or moves the whole content, on the
  // rail's state; nothing transitions `all`.
  for (const sheet of sheets) {
    sheet.walkDecls((d) => {
      const selector = d.parent.selector ?? '';
      if (/collapsed|data-rail|data-sidebar-closed/.test(selector)) assert.notEqual(d.prop, 'display', selector);
      if (d.prop === 'transition') assert.doesNotMatch(d.value, /(^|,)\s*all\b/, selector);
    });
  }
  for (const gone of ['.rail-shell[data-rail-moving] .rail-content', '.right-sidebar.collapsed .right-view-host', '.sidebar.collapsed .sidebar-nav-label']) {
    assert.equal(declarations(gone).size, 0, gone);
  }
});

test('the toggles ride the moving edge: one inset per state, moved on the rail\'s curve; their glyphs cross-fade', () => {
  const header = declarations('.rail-header');
  assert.equal(header.get('padding'), '8px');
  assert.equal(header.get('transition'), 'padding-left var(--rail-move-duration) var(--rail-move-easing), padding-right var(--rail-move-duration) var(--rail-move-easing)');
  // (62 - 16 - 30) / 2 = 8px: centred in the collapsed card.
  const centred = 'calc((var(--rail-collapsed-w) - 4 * var(--spacing) - var(--ui-list-h)) / 2)';
  assert.equal(declarations('.sidebar.collapsed:not(.right-sidebar) .rail-header').get('padding-right'), centred);
  assert.equal(declarations('.right-sidebar.collapsed .rail-header').get('padding-left'), centred);
  assert.equal(declarations('.sidebar:not(.right-sidebar) .rail-header').get('justify-content'), 'flex-end');
  assert.equal(declarations('.right-sidebar .rail-header').get('justify-content'), 'flex-start');
  assert.equal(declarations('.rail-toggle-glyphs > :is(.rail-toggle-close, .rail-toggle-open)').get('transition'), 'opacity var(--rail-move-duration) var(--rail-move-easing)');
  assert.equal(declarations('.rail-shell.collapsed .rail-toggle-close').get('opacity'), '0');
  assert.equal(declarations('.rail-shell:not(.collapsed) .rail-toggle-open').get('opacity'), '0');
});

test('a stored closed sidebar paints collapsed in the first frame, with nothing transitioning', () => {
  const shell = declarations('html[data-sidebar-closed] #sidebar');
  assert.equal(shell.get('width'), 'var(--rail-collapsed-w) !important');
  assert.equal(shell.get('transition'), 'none !important');
  const parts = declarations('html[data-sidebar-closed] #sidebar :is(.rail-open-only, .rail-row > .sidebar-nav-label, .rail-row > .sidebar-nav-text, .user-menu-footer-info, .user-menu-footer-chevron)');
  assert.deepEqual(Object.fromEntries(parts), { opacity: '0', transition: 'none' });
  // Out of reach before hydration, as `inert` keeps it after.
  assert.deepEqual(Object.fromEntries(declarations('html[data-sidebar-closed] #sidebar .rail-open-only')), { interactivity: 'inert' });
  const header = declarations('html[data-sidebar-closed] #sidebar .rail-header');
  assert.equal(header.get('padding-right'), declarations('.sidebar.collapsed:not(.right-sidebar) .rail-header').get('padding-right'));
  assert.equal(header.get('transition'), 'none');
  assert.equal(declarations('html[data-sidebar-closed] #sidebar .rail-toggle-close').get('opacity'), '0');
  assert.equal(declarations('html[data-sidebar-closed] #sidebar :is(.rail-toggle-close, .rail-toggle-open)').get('transition'), 'none');
});

test('the narrow layout fades the drawer shadow and returns a rail to the flow only once a close has settled', () => {
  const css = readFileSync(new URL('../../app/styles/base/layout.css', import.meta.url), 'utf8');
  assert.match(css, /\.sidebar > \[data-slot="sidebar-inner"\] \{\s*transition: box-shadow var\(--rail-move-duration\) var\(--rail-move-easing\);/);
  assert.match(css, /\.app:has\(> \.center-col\[data-split\]\) \.sidebar\[data-rail-closed\] \{\s*position: relative;/);
});

/** The last compound of a selector: what follows its last combinator
 *  outside brackets and parentheses. */
function target(selector) {
  let depth = 0; let cut = 0;
  for (let i = 0; i < selector.length; i += 1) {
    const ch = selector[i];
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth -= 1;
    else if (depth === 0 && /[\s>+~]/.test(ch)) cut = i + 1;
  }
  return selector.slice(cut).replace(/::?(before|after)$/, '');
}

/** A compound names its element by a class, id or attribute; an `:is()`
 *  does when each of its arguments' targets does. */
function named(compound) {
  const is = compound.match(/^:is\((.*)\)$/);
  if (!is) return /[.#[]/.test(compound);
  const args = []; let depth = 0; let from = 0;
  for (let i = 0; i < is[1].length; i += 1) {
    const ch = is[1][i];
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth -= 1;
    else if (ch === ',' && depth === 0) { args.push(is[1].slice(from, i)); from = i + 1; }
  }
  args.push(is[1].slice(from));
  return args.every((arg) => named(target(arg.trim())));
}

test('a rule keyed on the rail\'s state names its target by a class, an id or an attribute', () => {
  // A target such as `*` or `:last-child` makes the browser restyle the
  // rail's whole subtree on every change of the state: 4,500 elements and
  // about 27 ms of the left sidebar's click frame (traced).
  const found = [];
  for (const sheet of sheets) {
    sheet.walkRules((rule) => {
      for (const selector of rule.selectors ?? []) {
        if (!/\.collapsed|data-rail-|data-sidebar-closed/.test(selector)) continue;
        const last = target(selector);
        // The state's own element (`.sidebar.collapsed`, `.sidebar[data-rail-closed]`) is no descendant.
        if (/\.collapsed|data-rail-|data-sidebar-closed/.test(last)) continue;
        if (!named(last)) found.push(selector);
      }
    });
  }
  assert.deepEqual(found, []);
});

// Inherited properties: a change on an element restyles its whole subtree.
const INHERITED = /^(--|visibility$|pointer-events$|interactivity$|color$|cursor$|font|line-height$|letter-spacing$|word-spacing$|white-space|text-(align|indent|transform|shadow|rendering|wrap)|direction$|writing-mode$|quotes$|list-style|tab-size$|hyphens$|caret-color$|color-scheme$|accent-color$|user-select$|-webkit-text)/;

test('no move changes an inherited property: the open and collapsed rules differ only in properties of the element itself', () => {
  // A part whose `pointer-events` or `visibility` changed in the click's
  // frame restyled the whole conversation list under it: 1,764 and 1,946
  // elements (traced). The first paint of a stored closed sidebar
  // (html[data-sidebar-closed]) is no move.
  const found = [];
  for (const sheet of sheets) {
    sheet.walkRules((rule) => {
      const keyed = (rule.selectors ?? []).filter((sel) => /\.collapsed/.test(sel) && !/data-sidebar-closed/.test(sel));
      if (!keyed.length) return;
      rule.walkDecls((d) => { if (d.parent === rule && INHERITED.test(d.prop)) found.push(`${keyed.join(', ')} { ${d.prop} }`); });
    });
  }
  assert.deepEqual(found, []);
  // The open-only parts' open and collapsed rules, in particular.
  const open = declarations('.rail-open-only');
  const collapsed = declarations('.rail-shell.collapsed :is(.rail-open-only, .rail-row > .sidebar-nav-label, .rail-row > .sidebar-nav-text)');
  for (const prop of new Set([...open.keys(), ...collapsed.keys()])) {
    if (open.get(prop) !== collapsed.get(prop)) assert.doesNotMatch(prop, INHERITED, prop);
  }
});
