import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { registerHooks } from 'node:module';
import ts from 'typescript';
import { parseHTML } from 'linkedom';

const webRoot = new URL('../../', import.meta.url);
registerHooks({
  resolve(specifier, context, next) {
    const base = specifier.startsWith('@/') ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith('.') && !/\.[a-z]+$/i.test(specifier) ? new URL(specifier, context.parentURL).href : null;
    if (base) for (const suffix of ['.ts', '.tsx', '/index.ts', '/index.tsx']) {
      if (existsSync(fileURLToPath(base + suffix))) return { url: base + suffix, shortCircuit: true };
    }
    return next(specifier, context);
  },
  load(url, context, next) {
    let source;
    if (url.endsWith('/lib/i18n/index.ts')) source = 'export const useTranslation=()=>({text:(en)=>en});';
    if (url.endsWith('/components/ui/button.tsx')) source = 'export const buttonVariants=()=>"";';
    if (url.endsWith('/components/solar-icons/index.ts')) source = 'export const SolarIcon=()=>null;';
    if (url.endsWith('/lib/session-store/session-scope.tsx')) source = 'export const useOptionalScopedSessionId=()=>"s1";';
    if (url.endsWith('/lib/runtime-bridge/ui.ts')) source = 'export const closeAllPopovers=()=>{};';
    if (url.endsWith('/lib/desktop/bridge/bridge-api.ts')) source = 'export const desktopBridge=()=>({openExternal:u=>globalThis.gitOpened.push(u)});';
    if (url.endsWith('/lib/tabs/center-tabs-store.ts')) source = 'export const useCenterTabs=sel=>sel({openReviewTab:(...a)=>globalThis.gitReviews.push(a)});';
    if (url.endsWith('/components/ui/popover.tsx')) source = 'export const Popover=({children})=>children;export const PopoverTrigger=Popover;export const PopoverContent=Popover;';
    if (url.endsWith('/components/ui/tooltip.tsx')) source = 'export const HoverTip=({children})=>children; export const TipBody=()=>null;';
    if (url.endsWith('/lib/session-store/index.ts')) source = 'export const useSessionStore=sel=>sel(globalThis.gitStore);useSessionStore.getState=()=>globalThis.gitStore;';
    if (url.endsWith('/lib/net/ws/ws-request.ts')) source = 'export const wsRequest=(...args)=>globalThis.gitRequest(...args);';
    if (source) return { format: 'module', source, shortCircuit: true };
    if (url.endsWith('.tsx')) {
      return { format: 'module', shortCircuit: true, source: ts.transpileModule(readFileSync(fileURLToPath(url), 'utf8'), { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText };
    }
    return next(url, context);
  },
});

const { window } = parseHTML('<html><body></body></html>');
globalThis.window = window; globalThis.document = window.document;
globalThis.Event = window.Event; globalThis.CustomEvent = window.CustomEvent; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { GitChip } = await import('../../components/chat/top-bar/chips/git-chip.tsx');

const STATUS = {
  is_repo: true, root: '/repo', repo_name: 'repo', branch: 'feature', head: 'abc', upstream: null,
  ahead: 0, behind: 0, changes: { files: 2, untracked: 1, conflicts: 0, insertions: 3, deletions: 1 },
  is_worktree: false, default_branch: 'main', has_remote: true, gh_available: true,
  worktrees: [{ path: '/repo', branch: 'feature', is_main: true, is_current: true }],
  branches: ['feature', 'main'],
};

function setup(statusFor) {
  const calls = [];
  globalThis.gitStore = { runningTasks: {}, conversations: { s1: {} }, currentSessionId: 's1', setComposerInputFor: (k, v) => { globalThis.gitDraft = v; }, focusComposer() {}, activeChatKey: 's1' };
  globalThis.gitReviews = []; globalThis.gitOpened = [];
  globalThis.gitRequest = async (action, payload) => {
    calls.push([action, payload]);
    if (action === 'git_folder_status') return { path: payload.path, status: { path: payload.path, ...statusFor(payload.path) } };
    if (action === 'git_switch_branch') return { path: payload.path, ok: true, status: { ...STATUS, branch: payload.branch } };
    return { path: payload.path, ok: false, error: 'nope' };
  };
  return calls;
}

async function mount(elements) {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(h('div', null, ...elements)));
  await act(async () => {});
  return { host, unmount: async () => { await act(async () => root.unmount()); host.remove(); } };
}

test('one pill per checkout: a second folder in the same repository hides', async () => {
  setup(() => STATUS);
  const { host, unmount } = await mount([
    h(GitChip, { key: 'a', path: '/repo', order: 0 }),
    h(GitChip, { key: 'b', path: '/repo/sub', order: 1 }),
  ]);
  const pills = host.querySelectorAll('button.git-seg');
  assert.equal(pills.length, 1);
  assert.equal(host.querySelectorAll('.folder-pill-divider').length, 1, 'only the visible git half draws a divider');
  assert.match(pills[0].textContent, /feature/);
  assert.match(pills[0].textContent, /\+3/);
  assert.match(pills[0].textContent, /−1/);
  await unmount();
});

test('folders outside git render nothing', async () => {
  setup(() => ({ is_repo: false }));
  const { host, unmount } = await mount([h(GitChip, { path: '/plain', order: 0 })]);
  assert.equal(host.querySelector('button.git-seg'), null);
  await unmount();
});

test('a dirty folder opens branches in a worktree; review and the agent hand-off still work', async () => {
  const calls = setup(() => STATUS);
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0, onUseFolder: () => {} })]);
  const row = (label) => [...host.querySelectorAll('div')].find((el) => el.textContent.startsWith(label) && el.className.includes('cursor-pointer'));
  assert.match(host.textContent, /opens in a new worktree/, 'uncommitted changes send branches to a worktree');
  assert.deepEqual([...host.querySelectorAll('.git-menu-branches .menu-tag')].map((t) => t.textContent), ['current']);
  assert.doesNotMatch(host.textContent, /Work in/, 'no separate worktree section');
  await act(async () => row('main').dispatchEvent(new Event('click', { bubbles: true })));
  assert.deepEqual(calls.find(([a]) => a === 'git_create_worktree'), ['git_create_worktree', { path: '/repo', branch: 'main' }]);
  assert.equal(calls.find(([a]) => a === 'git_switch_branch'), undefined);
  await act(async () => row('2 uncommitted files').dispatchEvent(new Event('click', { bubbles: true })));
  assert.deepEqual(globalThis.gitReviews[0], ['s1', undefined, 'workspace']);
  await act(async () => row('Commit & open PR').dispatchEvent(new Event('click', { bubbles: true })));
  assert.match(globalThis.gitDraft, /pull request/);
  await unmount();
});

test('a clean folder switches in place', async () => {
  const calls = setup(() => ({ ...STATUS, changes: { files: 0, untracked: 0, conflicts: 0, insertions: 0, deletions: 0 } }));
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0, onUseFolder: () => {} })]);
  const row = (label) => [...host.querySelectorAll('div')].find((el) => el.textContent.startsWith(label) && el.className.includes('cursor-pointer'));
  assert.doesNotMatch(host.textContent, /opens in a new worktree/);
  assert.deepEqual([...host.querySelectorAll('.git-menu-branches .menu-tag')].map((t) => t.textContent), ['current']);
  await act(async () => row('main').dispatchEvent(new Event('click', { bubbles: true })));
  assert.deepEqual(calls.find(([a]) => a === 'git_switch_branch'), ['git_switch_branch', { path: '/repo', branch: 'main', create: false, carry: false }]);
  assert.equal(calls.find(([a]) => a === 'git_create_worktree'), undefined);
  await unmount();
});

test('a refused switch names the files and offers the worktree or carry routes', async () => {
  const calls = setup(() => ({ ...STATUS, changes: { files: 0, untracked: 0, conflicts: 0, insertions: 0, deletions: 0 } }));
  globalThis.gitRequest = (inner => async (action, payload) => {
    if (action === 'git_switch_branch' && !payload.carry) {
      calls.push([action, payload]);
      return { path: payload.path, ok: false, error: 'would overwrite', code: 'overwrite', files: ['deck.pptx'], detail: 'error: Your local changes...' };
    }
    return inner(action, payload);
  })(globalThis.gitRequest);
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0, onUseFolder: () => {} })]);
  const row = (label) => [...host.querySelectorAll('div')].find((el) => el.textContent.startsWith(label) && el.className.includes('cursor-pointer'));
  await act(async () => row('main').dispatchEvent(new Event('click', { bubbles: true })));
  const card = host.querySelector('.git-menu-error');
  assert.match(card.querySelector('.git-menu-error-title').textContent, /Can't switch to main: 1 file/);
  assert.equal(card.querySelector('.git-menu-error-files').textContent, 'deck.pptx');
  assert.equal(card.querySelector('details'), null, 'no native disclosure widget');
  assert.equal(card.querySelector('.git-menu-error-detail'), null);
  await act(async () => card.querySelector('.git-menu-error-more').dispatchEvent(new Event('click', { bubbles: true })));
  assert.match(card.querySelector('.git-menu-error-detail').textContent, /Your local changes/);
  const buttons = [...card.querySelectorAll('.git-menu-error-btn')].map((b) => b.textContent);
  assert.deepEqual(buttons, ['Open in a worktree', 'Carry the changes over']);
  await act(async () => card.querySelectorAll('.git-menu-error-btn')[1].dispatchEvent(new Event('click', { bubbles: true })));
  assert.deepEqual(calls.find(([a, p]) => a === 'git_switch_branch' && p.carry), ['git_switch_branch', { path: '/repo', branch: 'main', create: false, carry: true }]);
  assert.equal(host.querySelector('.git-menu-error'), null, 'the carry succeeded and cleared the card');
  await unmount();
});

test('binary-only changes show a file count instead of +0 −0', async () => {
  setup(() => ({ ...STATUS, changes: { files: 2, untracked: 1, conflicts: 0, insertions: 0, deletions: 0 } }));
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0 })]);
  const pill = host.querySelector('button.git-seg');
  assert.match(pill.textContent, /2 files/);
  assert.doesNotMatch(pill.textContent, /\+0/);
  assert.ok(pill.querySelector('.git-seg-dot.is-files'));
  await unmount();
});

test('an open pull request shows its checks and hands failing ones to the agent', async () => {
  // Shape as folder_git._open_pr returned it for a real pull request.
  const run = 'https://github.com/o/r/actions/runs/1/job/';
  const pr = {
    number: 14, url: 'https://github.com/o/r/pull/14', state: 'OPEN', title: 'build(deps): bump katex', isDraft: false,
    checks: {
      total: 20, passed: 13, pending: 0, failed_count: 7,
      failed: ['integration', 'windows-core (windows-2025, x64)', 'windows-core (windows-11-vs2026-arm, arm64)',
        'windows-desktop (windows-2025, x64)', 'windows-desktop (windows-11-vs2026-arm, arm64)', 'web', 'browser']
        .map((name, i) => ({ name, url: `${run}${i}` })),
    },
  };
  setup(() => ({ ...STATUS, pr }));
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0, onUseFolder: () => {} })]);
  const row = (label) => [...host.querySelectorAll('div')].find((el) => el.textContent.startsWith(label) && el.className.includes('cursor-pointer'));
  assert.ok(row('View pull request #14'));
  assert.equal(host.querySelector('.git-menu-note[aria-live="polite"]')?.textContent, 'Checks: 7 failing · 13 passed');
  await act(async () => row('Fix failing checks with the agent').dispatchEvent(new Event('click', { bubbles: true })));
  assert.match(globalThis.gitDraft, /^Pull request #14 \(https:\/\/github\.com\/o\/r\/pull\/14\) has failing CI checks: integration, windows-core/);
  assert.match(globalThis.gitDraft, /browser\. Read their logs with gh/);
  await unmount();
});

test('a pull request without failures offers no fix', async () => {
  setup(() => ({ ...STATUS, pr: { number: 3, url: 'https://github.com/o/r/pull/3', state: 'OPEN',
    checks: { total: 3, passed: 1, pending: 2, failed_count: 0, failed: [] } } }));
  const { host, unmount } = await mount([h(GitChip, { path: '/repo', order: 0, onUseFolder: () => {} })]);
  assert.equal(host.querySelector('.git-menu-note[aria-live="polite"]')?.textContent, 'Checks: 2 running · 1 passed');
  assert.doesNotMatch(host.textContent, /Fix failing checks/);
  await unmount();
});
