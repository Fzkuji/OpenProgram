// One chat starting to run, or moving to the top, re-renders the sidebar
// list. A row that is drawn the same does not render again, and still runs
// the handlers of the list's latest render (they close over its state).
import assert from 'node:assert/strict';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';

import { scratchDir } from '../../scratch-dir.mjs';

const web = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = scratchDir(web, 'conv-row-memo');
const stubs = {
  './status-marker': 'export const StatusMarker=({state})=>{ globalThis.rowRenders.push(state); return null; };',
};

test('a row renders again only when what it draws changes, and runs the latest handlers', async () => {
  await build({
    absWorkingDir: web,
    stdin: { contents: "export {ConvRow, sameConvRow} from './components/sidebar/sessions/conv-item';", resolveDir: web },
    bundle: true, format: 'esm', platform: 'node', packages: 'external', jsx: 'automatic',
    outfile: join(dir, 'test.mjs'), tsconfig: join(web, 'tsconfig.json'), loader: { '.css': 'empty' },
    plugins: [{ name: 'fixtures', setup(b) {
      b.onResolve({ filter: /.*/ }, (a) => (a.path in stubs ? { path: a.path, namespace: 'fixture' } : null));
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, (a) => ({ contents: stubs[a.path], resolveDir: web }));
    } }],
  });
  const { window } = parseHTML('<html><body></body></html>');
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.assign(globalThis, {
    window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements,
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    requestAnimationFrame: (fn) => setTimeout(fn, 0), IS_REACT_ACT_ENVIRONMENT: true,
  });
  globalThis.rowRenders = [];
  const { act, createElement } = await import('react');
  const { createRoot } = await import('react-dom/client');
  const { ConvRow, sameConvRow } = await import(pathToFileURL(join(dir, 'test.mjs')));

  const source = { current: new Map() };
  const clicked = [];
  const noop = () => {};
  // The list's render: fresh conversation objects, arrays and closures every time.
  function list(pass, running) {
    source.current = new Map();
    return ['a', 'b'].map((id) => {
      const props = {
        conv: { id, title: `Chat ${id}`, updated_at: pass }, label: `Chat ${id}`, active: id === 'a',
        running: running === id, groups: ['g'], projects: [{ id: 'p', name: 'P' }],
        dragProps: { style: { touchAction: 'none' }, 'data-chat-row': id, onPointerDown: noop },
        onClick: () => clicked.push(`${id}@${pass}`), onRename: noop, onAutoRename: noop, onTogglePin: noop,
        onToggleArchive: noop, onMoveToGroup: noop, onMoveToProject: noop, onCopyLink: noop, onExport: noop, onDelete: noop,
      };
      source.current.set(id, props);
      return createElement(ConvRow, { key: id, source, ...props });
    });
  }
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(list(1, null)));
    assert.equal(globalThis.rowRenders.length, 2);
    await act(async () => root.render(list(2, null)));
    assert.equal(globalThis.rowRenders.length, 2, 'nothing drawn changed: no row renders');
    await act(async () => host.querySelector('[data-chat-row="b"]').click());
    assert.deepEqual(clicked, ['b@2'], 'the click runs the latest render\'s handler');
    await act(async () => root.render(list(3, 'b')));
    assert.deepEqual(globalThis.rowRenders.slice(2), ['working'], 'only the chat that started running renders');
  } finally {
    await act(async () => root.unmount());
    host.remove();
  }
  const row = { conv: { id: 'a', title: 'T' }, label: 'T', active: false, running: false, groups: [] };
  assert.equal(sameConvRow(row, { ...row, conv: { id: 'a', title: 'T', updated_at: 9 } }), true);
  assert.equal(sameConvRow(row, { ...row, conv: { id: 'a', title: 'New' } }), false);
  assert.equal(sameConvRow(row, { ...row, dragProps: { 'data-chat-row': 'a' } }), false);
  assert.equal(sameConvRow(
    { ...row, dragProps: { 'data-chat-row': 'a', style: { transform: 'translateY(4px)' } } },
    { ...row, dragProps: { 'data-chat-row': 'a', style: { transform: 'translateY(8px)' } } },
  ), false);
});
