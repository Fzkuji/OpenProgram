import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { scratchDir } from "../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const temp = scratchDir(web, "programs-tree-test");
await build({
  absWorkingDir: web, entryPoints: ['components/programs/programs-page.tsx'], outfile: join(temp,'page.mjs'),
  bundle: true, packages: 'external', format: 'esm', platform: 'node', jsx: 'automatic',
  loader: { '.css': 'empty' }, plugins: [{ name: 'services', setup(b) {
    b.onResolve({ filter: /(?:net\/fetch-client|net\/applications|lib\/i18n|runtime-bridge\/state|abilities\/functions-store|net\/application-list|use-application-launcher|center-tabs-store|^next\/navigation$|^next\/link$|^\.\.\/file-management$|^\.\/file-tree-query$)/ }, a => ({ path: a.path, namespace: 'mock' }));
    b.onLoad({ filter: /.*/, namespace: 'mock' }, a => ({ resolveDir: web, contents:
      a.path.includes('fetch-client') ? 'export const jsonFetch = (...args) => globalThis.programFetch(...args); export class HttpError extends Error {}'
      : a.path.includes('net/applications') ? 'export const applicationRequest = async (...args) => { (globalThis.applicationCalls??=[]).push(args); return {}; };'
      : a.path.includes('application-list') ? 'export const useApplicationList=()=>globalThis.applicationList||{applications:[],builtin:[],loaded:true}; export const refreshApplications=async()=>[];'
      : a.path.includes('use-application-launcher') ? 'export const useApplicationLauncher=()=>({launch:async(app)=>{(globalThis.launched??=[]).push(app.id)},error:"",dialog:null});'
      : a.path.includes('center-tabs-store') ? 'export const useCenterTabs={getState:()=>({openBuiltinTab:(page)=>{(globalThis.openedTabs??=[]).push(page)}})};'
      : a.path.includes('i18n') ? 'const text=(en)=>en; const t=(key)=>key; export const useTranslation=()=>({text,t});'
      : a.path.includes('functions-store') ? 'const state={meta:{favorites:[],folders:{},icons:{}},setMeta(m){this.meta=m}};export const useFunctions={getState:()=>state};'
      : a.path.includes('runtime-bridge') ? 'export const runtimeState={};'
      // The Files tree's folder sizes; the Abilities tree shows none.
      : a.path === '../file-management' ? 'export const formatFileBytes=String; export const useFolderSize=()=>({value:{bytes:null,state:"idle"}});'
      : a.path === './file-tree-query' ? 'export const joinPath=(dir,name)=>dir?`${dir}/${name}`:name;'
      : a.path === 'next/link' ? 'import {createElement} from "react"; export default function Link(props){return createElement("a",props);}'
      : 'export const useRouter=()=>({push(){}}); export const usePathname=()=>"/programs";'
    }));
  } }],
});
const { window, document } = parseHTML('<html><body></body></html>');
Object.assign(globalThis,{window,document,IS_REACT_ACT_ENVIRONMENT:true});
// The tree is the Files panel's Pierre tree, which renders into a shadow root.
for (const key of ['Event','HTMLElement','HTMLDivElement','HTMLFormElement','ShadowRoot','HTMLStyleElement','Element','HTMLTemplateElement','SVGElement','HTMLInputElement','Node','MutationObserver','customElements']) globalThis[key] = window[key];
window.MouseEvent = window.Event;
Object.defineProperties(window.HTMLElement.prototype, {
  scrollTop: { configurable: true, writable: true, value: 0 },
  clientHeight: { configurable: true, get: () => 600 },
  clientWidth: { configurable: true, get: () => 300 },
});
window.HTMLElement.prototype.scrollTo = function(options) { this.scrollTop = options.top ?? this.scrollTop; };
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
globalThis.getComputedStyle=()=>({getPropertyValue:()=>'',display:'block'});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);
globalThis.cancelAnimationFrame=clearTimeout;
const intervals = new Set();
window.setInterval = fn => { intervals.add(fn); return fn; };
window.clearInterval = fn => intervals.delete(fn);
const { createElement, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { ProgramsPage } = await import(pathToFileURL(join(temp,'page.mjs')));

const app = (name, origin, extra = {}) => ({ name, path: `applications/${name}`, kind: 'folder', program_kind: 'application', origin, has_children: false, logic_path: `applications/${name}`, ...extra });
// The route lists applications alphabetically by directory name.
const listing = [
  app('auto_research', 'installed', {
    has_children: true, title: 'Auto Research', application_id: 'local.auto-research', icon: 'telescope', version: '1.2.0', form: 'package',
    description: 'Runs one task of a research project.',
    agent_entries: Array.from({ length: 8 }, (_, i) => ({ name: `task_${i}`, description: `Task ${i}.` })),
  }),
  app('browser', 'builtin', { has_children: true, enabled: true }),
  app('calculator', 'installed', {
    title: 'Calculator', application_id: 'example.calculator', icon: 'calculator', version: '2.0.0', form: 'application',
    description: 'A desktop-style calculator with history.', note: { kind: 'ui_only' }, enabled: true, agent_entries: [],
    source_path: '/Users/me/src/calculator', location: '~/src/calculator',
  }),
  app('demo_harness', 'installed', { has_children: true, callable_name: 'demo_agent' }),
  app('notes', 'builtin', { enabled: false }),
];
const children = {
  'applications/auto_research': [
    { name: 'app', path: 'applications/auto_research/app', kind: 'folder', program_kind: null, has_children: true },
    { name: 'stages', path: 'applications/auto_research/stages', kind: 'file', program_kind: null, has_children: false, logic_path: 'applications/auto_research/stages' },
  ],
  'applications/auto_research/app': [{ name: 'backend', path: 'applications/auto_research/app/backend', kind: 'file', program_kind: null, has_children: false, logic_path: 'applications/auto_research/app/backend' }],
  'applications/demo_harness': [{ name: 'demo_agent', path: 'applications/demo_harness/demo_agent', kind: 'file', program_kind: 'application', has_children: false, callable_name: 'demo_agent', logic_path: 'applications/demo_harness' }],
};
globalThis.programFetch = async url => {
  if (url.includes('/meta')) return { favorites: [], folders: {}, icons: {} };
  const u = new URL(url, 'http://local');
  const path = u.searchParams.get('path') || '';
  if (u.pathname.endsWith('/explorer')) {
    if (!path) return { revision: '1', entries: ['tools', 'applications'].map(name => ({ name, path: name, kind: 'folder', program_kind: null, has_children: true })), default_selection: 'applications/auto_research' };
    if (path === 'tools') return { revision: '1', entries: [{
      name: 'read', path: 'tools/read', kind: 'file', program_kind: 'tool', has_children: false, callable_name: 'read', logic_path: 'tools/read',
      description: 'Read a file.', parameters: [{ name: 'path', description: 'File to read', required: true }, { name: 'limit', description: 'Lines', required: false }],
    }] };
    if (path === 'applications') return { revision: '1', entries: listing };
    if (children[path]) return { revision: '1', entries: children[path] };
    throw Object.assign(new Error('missing'), { status: 404 });
  }
  const entries = path === 'applications/auto_research' ? ['task_0', 'task_1', 'task_2'] : undefined;
  return { revision: '1', root: path, nodes: [{ id: path, name: path.split('/').at(-1), path, program_kind: 'application', depth: 0 }], edges: [], entries };
};
// Pierre draws on animation frames (timers here), so settling lets timers run too.
async function settle() { for (let i = 0; i < 8; i++) await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
const click = async element => act(async () => element.dispatchEvent(new window.MouseEvent('click', { bubbles: true, composed: true })));
// Pierre's rows: a folder's path ends with "/".
const rowsOf = host => [...(host.querySelector('file-tree-container')?.shadowRoot?.querySelectorAll('[role="treeitem"][data-item-path]') ?? [])];
const rowOf = (host, path) => rowsOf(host).find(el => el.getAttribute('data-item-path').replace(/\/$/, '') === path) ?? null;

test('Applications is a plain folder listing: directory names, alphabetical, folder icons', async () => {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ProgramsPage)));
    await settle();
    const text = host.querySelector('[data-testid="programs-explorer"]').textContent + rowsOf(host).map(el => el.textContent).join(' ');
    assert.ok(!text.includes('Built in') && !text.includes('Installed'));
    const order = rowsOf(host).map(el => el.getAttribute('data-item-path')).filter(path => path.startsWith('applications/') && path !== 'applications/');
    assert.deepEqual(order, listing.map(entry => `${entry.path}/`));
    // Every application row is a folder named by its directory, never dimmed.
    for (const entry of listing) {
      const row = rowOf(host, entry.path);
      assert.equal(row.getAttribute('aria-label'), entry.name);
      assert.equal(row.getAttribute('data-item-type'), 'folder', entry.path);
    }
    // No hover tips and no Size column on the tree.
    assert.deepEqual(rowsOf(host).filter(el => el.querySelector('[title], [data-tip]') || el.hasAttribute('title')).map(el => el.getAttribute('data-item-path')), []);
    assert.equal(rowsOf(host).filter(el => el.querySelector('[data-item-section="decoration"]')?.textContent).length, 0);
    assert.equal(host.querySelector('[role="columnheader"]'), null);
    // The default selection is the selected row.
    assert.equal(rowOf(host, 'applications/auto_research').getAttribute('aria-selected'), 'true');

    // Expanding a folder shows its real subfolders and files.
    await click(rowOf(host, 'applications/auto_research'));
    await settle();
    assert.equal(rowOf(host, 'applications/auto_research/app').getAttribute('data-item-type'), 'folder');
    assert.equal(rowOf(host, 'applications/auto_research/stages').getAttribute('data-item-type'), 'file');
    await click(rowOf(host, 'applications/auto_research/app'));
    await settle();
    assert.ok(rowOf(host, 'applications/auto_research/app/backend'));
    // Pierre's own indent guides: one per level, always shown, in the theme's colour.
    const guides = path => rowOf(host, path).querySelectorAll('[data-item-section="spacing-item"]').length;
    assert.equal(guides('applications/auto_research'), 1);
    assert.equal(guides('applications/auto_research/app/backend'), 3);
    const css = [...host.querySelector('file-tree-container').shadowRoot.querySelectorAll('style')].map(el => el.textContent).join('\n');
    assert.match(css, /--trees-indent-guide-bg-override: color-mix\(in srgb, var\(--text-muted\) 60%, transparent\)/);
    assert.match(css, /\[data-item-section="spacing-item"\] \{ opacity: 1; \}/);

    await click(rowOf(host, 'applications/demo_harness'));
    await settle();
    assert.ok(rowOf(host, 'applications/demo_harness/demo_agent'));
    assert.match(host.querySelector('h2').textContent, /demo_harness/);
    // A callable shares its package's logic path, but the clicked row is the selected one.
    await click(rowOf(host, 'applications/demo_harness/demo_agent'));
    await settle();
    assert.equal(rowOf(host, 'applications/demo_harness/demo_agent').getAttribute('aria-selected'), 'true');
    assert.equal(rowOf(host, 'applications/demo_harness').getAttribute('aria-selected'), 'false');
    // A file inside a package keeps its file header over its call logic.
    await click(rowOf(host, 'applications/auto_research/stages'));
    await settle();
    assert.match(host.querySelector('h2').textContent, /stages/);
    assert.ok(host.querySelector('[data-testid="programs-call-tree"]'));
    assert.equal(host.querySelector('[data-testid="programs-call-logic"]'), null);
  } finally { await act(async () => root.unmount()); host.remove(); }
});

test('an application detail shows its icon, title, description, entries and folded call logic', async () => {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  globalThis.applicationList = { loaded: true, builtin: [], applications: [
    { id: 'example.calculator', title: 'Calculator', scope: 'global', capabilities: ['storage.app'], enabled: true, ui: { root: 'ui', entry: 'index.html' } },
  ] };
  globalThis.launched = []; globalThis.applicationCalls = [];
  try {
    await act(async () => root.render(createElement(ProgramsPage)));
    await settle();
    // The default selection: a package with a window and many entries.
    await click(rowOf(host, 'applications/auto_research'));
    await settle();
    assert.equal(host.querySelector('h2').textContent, 'Auto Research');
    assert.match(host.textContent, /auto_research.*Installed application.*v1\.2\.0/);
    assert.match(host.textContent, /Runs one task of a research project\./);
    assert.ok(host.querySelector('svg'), 'the application icon');
    const entries = host.querySelector('[data-testid="programs-agent-entries"]');
    assert.match(entries.textContent, /Agent can call8/);
    assert.equal(entries.querySelectorAll('code').length, 6);
    const showAll = [...entries.querySelectorAll('button')].find(button => button.textContent === 'Show all 8');
    await click(showAll);
    assert.equal(entries.querySelectorAll('code').length, 8);
    const disclosure = host.querySelector('[data-testid="programs-call-logic"]');
    assert.equal(disclosure.tagName, 'DETAILS');
    assert.equal(disclosure.hasAttribute('open'), false);
    assert.match(disclosure.querySelector('summary').textContent, /Call logic3 entries/);
    assert.ok(disclosure.querySelector('[data-testid="programs-call-tree"]'));
    // No more node/edge chips or the big "No Agent entry" box.
    assert.doesNotMatch(host.textContent, /reachable nodes|direct calls/);

    await click(rowOf(host, 'applications/calculator'));
    await settle();
    assert.equal(host.querySelector('h2').textContent, 'Calculator');
    assert.match(host.querySelector('[data-testid="programs-application-note"]').textContent, /The Agent cannot call this application; it only opens as a window\./);
    assert.match(host.textContent, /no calls to other Programs/);
    assert.match(host.textContent, /Location~\/src\/calculator/);
    assert.match(host.textContent, /Keep its own data/);
    const buttons = [...host.querySelectorAll('button')];
    assert.ok(!buttons.some(button => button.textContent === 'Use in chat'));
    await click(buttons.find(button => button.textContent === 'Open'));
    assert.deepEqual(globalThis.launched, ['example.calculator']);
    const enabled = host.querySelector('button[role="switch"][aria-label="Enabled"]');
    assert.ok(enabled);
    await click(enabled);
    await settle();
    assert.deepEqual(globalThis.applicationCalls[0], ['/api/applications/example.calculator', 'PATCH', { enabled: false }]);

    // A tool lists its parameters instead of entries.
    await click(rowOf(host, 'tools'));
    await settle();
    await click(rowOf(host, 'tools/read'));
    await settle();
    assert.equal(host.querySelector('h2').textContent, 'read');
    assert.match(host.textContent, /Built-in tool/);
    const parameters = host.querySelector('[data-testid="programs-parameters"]');
    assert.match(parameters.textContent, /Parameters2/);
    assert.match(parameters.textContent, /limitoptional/);
    assert.ok([...host.querySelectorAll('button')].some(button => button.textContent === 'Use in chat'));
  } finally { await act(async () => root.unmount()); host.remove(); delete globalThis.applicationList; }
});

test('filtering shows the matches under their folders, as Files does', async () => {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ProgramsPage, { embedded: true, query: 'calc' })));
    await settle();
    assert.deepEqual(rowsOf(host).map(el => el.getAttribute('data-item-path')), ['applications/', 'applications/calculator/']);
    await act(async () => root.render(createElement(ProgramsPage, { embedded: true, query: 'zzz' })));
    await settle();
    assert.match(host.querySelector('[data-testid="programs-explorer"]').textContent, /No matches/);
  } finally { await act(async () => root.unmount()); host.remove(); }
});
