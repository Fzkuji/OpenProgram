// The left sidebar's avatar glides between its two resting places while the
// rail opens or closes: the trigger's left padding is 16px open and 5px
// collapsed, transitioned on the rail's own duration and curve, and its right
// padding moves from 16px to 27px with it, so the name keeps one width. The
// footer sits beside .rail-content in the card and is laid out at the open
// width in both states, so nothing clips the avatar on its way and nothing
// in it re-wraps; the name and the chevron fade.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';
import postcss from 'postcss';

import { scratchDir } from '../scratch-dir.mjs';

const read = (path) => readFileSync(new URL(`../../${path}`, import.meta.url), 'utf8');
const sheets = {
  module: postcss.parse(read('components/user-menu-footer.module.css')),
  sidebar: postcss.parse(read('app/styles/base/sidebar.css')),
};
function declarations(sheet, selector) {
  const found = new Map();
  sheets[sheet].walkRules((rule) => {
    if (rule.selectors?.includes(selector)) rule.walkDecls((d) => { found.set(d.prop, d.value); });
  });
  return found;
}
const hookSource = read('components/layout/use-resizable-rail.ts');
const RAIL_MOVE_MS = Number(hookSource.match(/const RAIL_MOVE_MS = (\d+);/)[1]);
const RAIL_EASING = hookSource.match(/const RAIL_EASING = "([^"]+)";/)[1];

test('the trigger rests at 16px open and 5px collapsed, the values the old width clamp gave', () => {
  const trigger = declarations('module', '.trigger');
  assert.equal(trigger.get('padding-left'), '16px');
  assert.equal(trigger.get('padding-right'), '16px');
  const collapsed = declarations('sidebar', '.sidebar.collapsed .user-menu-footer-trigger');
  assert.equal(collapsed.get('padding-left'), '5px');
  // The content box keeps its width: what follows the avatar moves with it.
  assert.equal(collapsed.get('padding-right'), '27px');
  // max(5px, min(16px, 100% - 46px)) at the collapsed card (46px) and at
  // every open card the resize handle allows (180..480 minus the gutter).
  const clamp = (w) => Math.max(5, Math.min(16, w - 46));
  assert.equal(clamp(46), 5);
  for (let w = 164; w <= 464; w += 1) assert.equal(clamp(w), 16);
  // Before hydration the forced-collapsed sidebar shows the collapsed place
  // at once and does not animate into it.
  const early = declarations('sidebar', 'html[data-sidebar-closed] #sidebar .user-menu-footer-trigger');
  assert.equal(early.get('padding-left'), '5px');
  assert.equal(early.get('padding-right'), '27px');
  assert.equal(early.get('transition'), 'none');
});

test('the padding moves on the rail\'s own duration and curve, read from the rail', () => {
  const transition = declarations('module', '.trigger').get('transition').replace(/\s+/g, ' ');
  assert.match(transition, /padding-left var\(--rail-move-duration, 0s\) var\(--rail-move-easing, linear\)/);
  assert.match(transition, /padding-right var\(--rail-move-duration, 0s\) var\(--rail-move-easing, linear\)/);
  assert.match(transition, /^background /, 'the hover fill keeps its own transition');
  assert.match(hookSource, /"--rail-move-duration": `\$\{RAIL_MOVE_MS\}ms`/);
  assert.match(hookSource, /"--rail-move-easing": RAIL_EASING/);
  assert.match(hookSource, /const RAIL_TRANSITION = `width \$\{RAIL_MOVE_MS\}ms \$\{RAIL_EASING\}/);
});

test('the footer is laid out at the open width in both states, and only its width is set', () => {
  const held = declarations('sidebar', '.sidebar.rail-shell .user-menu-footer');
  assert.deepEqual([...held.keys()].sort(), ['align-self', 'width']);
  assert.equal(held.get('width'), 'var(--rail-open-card-w)');
  assert.equal(held.get('align-self'), 'flex-start');
  assert.match(hookSource, /"--rail-open-w": `\$\{shownWidth\}px`/);
});

test('the name and the chevron fade by opacity alone, never switched off', () => {
  // Clipped by the card beyond the collapsed card's edge, inside the
  // trigger, which stays reachable; no inherited property changes on a move.
  for (const part of ['.user-menu-footer-info', '.user-menu-footer-chevron']) {
    const collapsed = declarations('sidebar', `.sidebar.collapsed ${part}`);
    assert.deepEqual(Object.fromEntries(collapsed), { opacity: '0' }, part);
  }
  assert.match(declarations('sidebar', '.user-menu-footer-info').get('transition').replace(/\s+/g, ' '), /^opacity var\(--motion-medium\) cubic-bezier\([^)]*\)$/);
  assert.equal(declarations('sidebar', '.sidebar .user-menu-footer-chevron').get('transition').replace(/\s+/g, ' '), 'opacity var(--motion-medium) var(--ease-standard)');
});

/** cubic-bezier(x1, y1, x2, y2) as progress over time, by bisection. */
function bezier(easing) {
  const [x1, y1, x2, y2] = easing.match(/[\d.]+/g).map(Number);
  const at = (a, b, t) => 3 * a * t * (1 - t) ** 2 + 3 * b * t ** 2 * (1 - t) + t ** 3;
  return (time) => {
    let lo = 0; let hi = 1;
    for (let i = 0; i < 40; i += 1) { const mid = (lo + hi) / 2; if (at(x1, x2, mid) < time) lo = mid; else hi = mid; }
    return at(y1, y2, (lo + hi) / 2);
  };
}

test('the avatar never reaches the card\'s moving edge and never steps more than a frame of travel', () => {
  const progress = bezier(RAIL_EASING);
  const AVATAR = 36;
  for (const open of [164, 272, 464]) {
    for (const [from, to] of [[[46, 5], [open, 16]], [[open, 16], [46, 5]]]) {
      let last = from[1];
      let largest = 0;
      for (let ms = 0; ms <= RAIL_MOVE_MS; ms += 1000 / 60) {
        const s = progress(Math.min(1, ms / RAIL_MOVE_MS));
        const card = from[0] + (to[0] - from[0]) * s;
        const pad = from[1] + (to[1] - from[1]) * s;
        assert.ok(pad + AVATAR <= card + 1e-9, `avatar leaves the ${card}px card at ${ms}ms`);
        largest = Math.max(largest, Math.abs(pad - last));
        last = pad;
      }
      // 11px over 200ms at 60 Hz: about 2.2px in the steepest frame of the curve.
      assert.ok(largest < 2.5, `largest step ${largest}px`);
    }
  }
});

// The footer is the card's last child, beside .rail-content, never in it.
const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(web, 'rail-footer');
const stubs = {
  'next/navigation': 'export const usePathname=()=>"/s/a";',
  '@/lib/i18n': 'const tr={t:x=>x,text:x=>x}; export const useTranslation=()=>tr;',
  '../animated-icons': 'import {forwardRef} from "react"; const Icon=forwardRef(()=>null); export const PanelLeftCloseIcon=Icon,PanelLeftOpenIcon=Icon,PlusIcon=Icon;',
  'framer-motion': 'import React,{forwardRef} from "react"; export const motion={div:forwardRef(({layoutScroll,...props},ref)=>React.createElement("div",{...props,ref}))};',
  '../user-menu-footer': 'import React from "react"; export const UserMenuFooter=()=>React.createElement("div",{className:"user-menu-footer"});',
  './sessions/sessions-list': 'export const SessionsList=()=>null;',
  './sessions/favorites-list': 'export const FavoritesList=()=>null;',
  './sessions/section-header': 'export const SectionHeader=()=>null;',
  './sidebar-primary-nav': 'export const SidebarPrimaryNav=()=>null;',
  './use-window-globals': 'export const useWindowGlobals=()=>({availableFunctions:[],programsMeta:{}});',
  '@/lib/runtime-bridge/state': 'export const runtimeState={};',
  '@/lib/tabs/navigation/open-session': 'export const openNewChat=()=>"draft";',
  './use-stable-scroll-end': 'export const useStableScrollEnd=()=>{};',
};

test('the footer is the card\'s last child, beside the held content, and the shell carries the rail\'s timing', async () => {
  await build({
    absWorkingDir: web,
    stdin: { contents: "export {Sidebar} from './components/sidebar/sidebar';", resolveDir: web },
    bundle: true, format: 'esm', platform: 'node', packages: 'external', jsx: 'automatic',
    outfile: join(dir, 'test.mjs'), tsconfig: join(web, 'tsconfig.json'), loader: { '.css': 'empty' },
    plugins: [{ name: 'fixtures', setup(b) {
      b.onResolve({ filter: /.*/ }, (a) => (a.path in stubs ? { path: a.path, namespace: 'fixture' } : null));
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, (a) => ({ contents: stubs[a.path], resolveDir: web }));
    } }],
  });
  const { window } = parseHTML('<html><body></body></html>');
  Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });
  globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
  window.location = { pathname: '/s/a' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  // The rail marks its first drawn frame (use-resizable-rail.ts).
  window.requestAnimationFrame = () => 0;
  window.cancelAnimationFrame = () => {};
  const { act, createElement } = await import('react');
  const { createRoot } = await import('react-dom/client');
  const { Sidebar } = await import(pathToFileURL(join(dir, 'test.mjs')));
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(Sidebar)));
    const card = host.querySelector('[data-slot="sidebar-inner"]');
    // (The resize handle before them is absolutely positioned.)
    assert.deepEqual([...card.children].slice(-2).map((child) => child.className), ['rail-content', 'user-menu-footer']);
    assert.equal(host.querySelector('.rail-content .user-menu-footer'), null);
    const style = host.querySelector('#sidebar').getAttribute('style') ?? '';
    assert.match(style, new RegExp(`--rail-move-duration: ?${RAIL_MOVE_MS}ms`));
    assert.match(style, /--rail-open-w: ?288px/);
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
});

test('a favourite row is moved, never scaled, when the sidebar changes width', () => {
  // Reorder.Item animates size by default: a drag of the rail's edge stretched the row's text.
  assert.match(read('components/sidebar/sessions/favorites-list.tsx'), /<Reorder\.Item[^>]*?layout="position"/s);
});
