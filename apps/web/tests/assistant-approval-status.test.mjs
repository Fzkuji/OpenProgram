import assert from 'node:assert/strict';
import test, { after } from 'node:test';
import { mkdtemp, rm } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const web = dirname(fileURLToPath(new URL('../package.json', import.meta.url)));
const dir = await mkdtemp(join(web, '.approval-status-'));
after(() => rm(dir, { recursive: true, force: true }));
const file = join(dir, 'bubble.mjs');
await build({
  absWorkingDir: web, entryPoints: ['components/chat/messages/assistant-bubble.tsx'],
  bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic', outfile: file,
  plugins: [{ name: 'bubble-services', setup(b) {
    b.onResolve({ filter: /^(@\/|\.\/)/ }, a => a.importer.endsWith('assistant-bubble.tsx') ? { path: a.path, namespace: 'stub' } : null);
    b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ contents:
      a.path.includes('session-store') ? 'export const useSessionStore = selector => selector(globalThis.approvalState);' :
      a.path.includes('agent-style') ? 'export const agentColor=()=>"", agentInitial=()=>"", agentDisplayName=()=>"Agent", useAgentProfile=()=>({name:"Agent"});' :
      a.path.includes('i18n') ? 'export const useTranslation=()=>({text:(en)=>en});' :
      a.path.includes('use-avatar-align') ? 'export const useAvatarAlign=()=>({containerRef:null,avatarTop:0});' :
      a.path.endsWith('/markdown') ? 'export const useMarkdownReady=()=>{}, renderMarkdown=t=>t;' :
      a.path.includes('user-attachments') ? 'export const parseAttachments=text=>({attachments:[],text}), AttachmentChips=()=>null;' :
      a.path.includes('turn-files-presentation') ? 'export const shouldRenderTurnFiles=()=>false;' :
      'export const Avatar=()=>null, AttachCard=()=>null, ExecutionStrip=()=>null, execStripLabel=()=>"", FunctionStep=()=>null, SPAWNING_TOOL_NAMES=new Set(), SubAgentStep=()=>null, ThinkingStep=()=>null, MessageActions=()=>null, MessageTimestamp=()=>null, RuntimeBlock=()=>null, TurnFilesChips=()=>null;'
    }));
  }}],
});
const { AssistantBubble } = await import(pathToFileURL(file));
const decision = { kind:'approval', sessionId:'s1', executionId:'e1' };
const order = { sessionId:'s1', messageIds:['m1'], terminal:false };
function render(decisions, orders, sessionId='s1', messageId='m1') {
  globalThis.approvalState={currentSessionId:sessionId,pendingDecisions:decisions,executionUpdateOrders:orders};
  return renderToStaticMarkup(createElement(AssistantBubble,{msg:{id:messageId,content:'',status:'running'},sessionIdOverride:sessionId}));
}
test('only the canonical approval owner shows a waiting status, removed on resolution', () => {
  assert.match(render([decision],{e1:order}), /Waiting for approval/);
  assert.doesNotMatch(render([],{e1:order}), /Waiting for approval/);
  assert.doesNotMatch(render([decision],{e1:order},'s1','old-message'), /Waiting for approval/);
  assert.doesNotMatch(render([decision],{e1:order},'s2'), /Waiting for approval/);
  assert.doesNotMatch(render([decision],{e1:{...order,sessionId:'s2'}}), /Waiting for approval/);
  assert.doesNotMatch(render([decision],{e1:{...order,terminal:true}}), /Waiting for approval/);
  assert.doesNotMatch(render([{...decision,kind:'ask'}],{e1:order}), /Waiting for approval/);
  assert.doesNotMatch(render([decision],{}), /Waiting for approval/);
});
