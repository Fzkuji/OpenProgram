// The sessions list unmounts with the collapsed sidebar and mounts again when
// it opens. The project list and the collapsed projects it starts from match
// what the server and the browser then send, so a reply that brings nothing
// new does not render the list again (and restyle every row) while the
// sidebar is still sliding open.
import assert from 'node:assert/strict';
import test from 'node:test';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { parseHTML } from 'linkedom';

import { scratchDir } from '../scratch-dir.mjs';

const web = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const dir = scratchDir(web, 'sessions-remount');
const stubs = {
  'next/navigation': 'export const usePathname=()=>"/s/a"; export const useRouter=()=>({push(){},replace(){}});',
  '../use-window-globals': 'export const useCurrentSessionId=()=>{ globalThis.listRenders.count+=1; return "a"; };',
  '@/lib/net/ws/ws-request': 'export const replies={count:0,projects:[]}; export async function wsRequest(){ replies.count+=1; return {projects: replies.projects.map((p)=>({...p}))}; } export const wsFire=()=>{}; export const wsListen=()=>()=>{};',
};

test('mounting again with the same projects and collapsed set renders the list once', async () => {
  await build({
    absWorkingDir: web,
    stdin: { contents: "export {SessionsList} from './components/sidebar/sessions/sessions-list'; export {replies} from '@/lib/net/ws/ws-request';", resolveDir: web },
    bundle: true, format: 'esm', platform: 'node', packages: 'external', jsx: 'automatic',
    outfile: join(dir, 'test.mjs'), tsconfig: join(web, 'tsconfig.json'), loader: { '.css': 'empty' },
    plugins: [{ name: 'fixtures', setup(b) {
      b.onResolve({ filter: /.*/ }, (a) => (a.path in stubs ? { path: a.path, namespace: 'fixture' } : null));
      b.onLoad({ filter: /.*/, namespace: 'fixture' }, (a) => ({ contents: stubs[a.path], resolveDir: web }));
    } }],
  });
  const { window } = parseHTML('<html><body></body></html>');
  const stored = new Map([['sidebar_collapsed_projects', JSON.stringify(['p2'])]]);
  globalThis.localStorage = { getItem: (k) => stored.get(k) ?? null, setItem: (k, v) => stored.set(k, v), removeItem: (k) => stored.delete(k) };
  window.location = { pathname: '/s/a', search: '', hash: '', href: 'http://localhost/s/a' };
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
  Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements, IS_REACT_ACT_ENVIRONMENT: true });
  const { act, createElement } = await import('react');
  const { createRoot } = await import('react-dom/client');
  const listRenders = { count: 0 };
  globalThis.listRenders = listRenders;
  const { SessionsList, replies } = await import(pathToFileURL(join(dir, 'test.mjs')));
  replies.projects = [{ id: 'p1', name: 'One', session_ids: ['a'] }, { id: 'p2', name: 'Two', session_ids: [] }];

  async function mountOnce() {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    const before = listRenders.count;
    const tree = createElement(SessionsList, { onNewChat: () => 'draft' });
    await act(async () => root.render(tree));
    await act(async () => { await new Promise((r) => setImmediate(r)); });
    await act(async () => root.unmount());
    host.remove();
    return listRenders.count - before;
  }

  const first = await mountOnce();
  assert.ok(replies.count >= 1, 'the list asked for the projects');
  const asked = replies.count;
  const again = await mountOnce();
  assert.ok(replies.count > asked, 'mounting again asks again');
  assert.ok(first > 1, `the first mount takes the reply in (${first} renders)`);
  assert.equal(again, 1, 'the remount renders once');
});
