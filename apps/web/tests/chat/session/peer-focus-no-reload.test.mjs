import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = await mkdtemp(join(root, '.peer-focus-test-'));
after(() => rm(dir, { recursive: true, force: true }));
await build({absWorkingDir:root,stdin:{contents:`
export {peerPresentsSession,registerHistoryViewport} from './lib/runtime-bridge/session/session-history-loader';
`,resolveDir:root},bundle:true,format:'esm',platform:'node',packages:'external',tsconfig:join(root,'tsconfig.json'),outfile:join(dir,'test.mjs')});
const {peerPresentsSession,registerHistoryViewport}=await import(pathToFileURL(join(dir,'test.mjs')));

test('only a split pane viewport counts as presenting a session', () => {
  const releaseShell = registerHistoryViewport('a', {}, 'a');
  assert.equal(peerPresentsSession('a'), false);
  const releasePeer = registerHistoryViewport('a', {}, 'peer:a');
  assert.equal(peerPresentsSession('a'), true);
  releasePeer();
  assert.equal(peerPresentsSession('a'), false);
  releaseShell();
});

test('focusing a split pane does not reload the session it already shows', async () => {
  // A route load resets the pane's history window to the latest page and
  // back, which shrinks the transcript and flashes Jump to latest.
  const shell = await readFile(join(root, 'components/page-shell.tsx'), 'utf8');
  const guard = shell.indexOf('if (peerPresentsSession(target))');
  assert.ok(guard > 0);
  assert.ok(guard < shell.indexOf('requestSessionLoad({ action: "load_session", session_id: target })'));
  assert.match(shell.slice(guard, guard + 160), /focusLoadedSession\(target\)[\s\S]*return;/);
});
