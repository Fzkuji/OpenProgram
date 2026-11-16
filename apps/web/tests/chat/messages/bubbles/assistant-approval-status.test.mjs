import assert from 'node:assert/strict';
import test from "node:test";
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { scratchDir } from "../../../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../../../package.json', import.meta.url)));
const dir = scratchDir(web, "approval-status");
const file = join(dir, 'bubble.mjs');
await build({
  absWorkingDir: web, entryPoints: ['components/chat/messages/bubbles/assistant-bubble.tsx'],
  external: ['react'], bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic', outfile: file,
  plugins: [{ name: 'bubble-services', setup(b) {
    b.onResolve({ filter: /^(@\/|\.\.?\/)/ }, a => a.importer.endsWith('assistant-bubble.tsx') && !['@/lib/format-utils/format','@/lib/chat/transcript/owned-children','@/lib/chat/transcript/row-status','@/lib/chat/transcript/task-blocks'].includes(a.path) ? { path: a.path, namespace: 'stub' } : null);
    b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ resolveDir: web, contents:
      a.path.includes('session-store') ? 'export const useSessionStore = selector => selector(globalThis.approvalState); export const selectRow = () => () => undefined, selectViewRows = () => () => ({});' :
      a.path.includes('pending-steers') ? 'export const PendingSteers=()=>null;' :
      a.path.includes('rewind-error-actions') ? 'export const RewindErrorActions=()=>null; export const useRewindOffered=()=>false;' :
      a.path.includes('agent-style') ? 'export const agentColor=()=>"", agentInitial=()=>"", agentDisplayName=()=>"Agent", useAgentProfile=()=>({name:"Agent"});' :
      a.path.includes('i18n') ? 'export const useTranslation=()=>({text:(en)=>en});' :
      a.path.includes('use-avatar-align') ? 'export const useAvatarAlign=()=>({containerRef:null,avatarTop:0});' :
      a.path.endsWith('/markdown') ? 'export const useMarkdownReady=()=>{}, renderMarkdown=t=>t;' :
      a.path.includes('streaming-blocks') ? 'export const splitMarkdownBlocks=t=>[t];' :
      a.path.includes('markdown-render') ? 'export const typesetMath=()=>{};' :
      a.path.includes('mermaid') ? 'export const renderMermaidLazily=()=>{};' :
      a.path.includes('visual-frames') ? 'export const mountVisualsIn=()=>{};' :
      a.path.includes('user-attachments') ? 'export const parseAttachments=text=>({attachments:[],text}), AttachmentChips=()=>null;' :
      a.path.includes('turn-files-presentation') ? 'export const shouldRenderTurnFiles=()=>false;' :
      a.path.includes('assistant-file-cards') ? 'export const AssistantFileCards=()=>null; export const sentFilePath=()=>null;' :
      a.path.includes('prose-file-links') ? 'export const ProseFileLinks=({children})=>children;' :
      // Provider-run web searches and their citations are not part of these renders.
      a.path.includes('web-sources') ? 'export const CitationSources=()=>null, isProviderSearch=()=>false;' :
      // A restart's continuation: these tests render single replies with no predecessor.
      a.path.includes('restart-continuation') ? 'export const continuedRows=()=>[], fallbackBlocks=()=>[], joinedReply=(cut,reply)=>reply;' :
      // The thinking status as a marker the tests look for.
      // The retry pieces as markers: where each lands is what these tests check.
      a.path.includes('retry-status') ? 'import {createElement} from "react"; export const RetryTag=({retry})=>createElement("span",{className:"retry-tag"},retry.reason||""), RetryStep=({retry})=>createElement("span",{className:"retry-step"},retry.reason||""), RetryStrip=({retry})=>createElement("div",{className:"retry-strip"},retry.reason||"");' :
      // These replies declare no task: the real layout runs over no segments.
      a.path.includes('use-row-tasks') ? 'export const useRowTasks=()=>({segments:[],legacy:[],earlierAnchor:{}});' :
      a.path.includes('/task-block') ? 'export const TaskBlock=()=>null;' :
      a.path.includes('transcript-view-scope') ? 'export const useRowSessionId=(own)=>own??globalThis.approvalState.currentSessionId;' :
      a.path.includes('thinking-status') ? 'import {createElement} from "react"; export const ThinkingStatus=()=>createElement("span",{className:"thinking-status"});' :
      'import {createElement} from "react"; export const Avatar=()=>null, AgentAvatarById=()=>null, CredentialNotices=()=>null, UserBubble=()=>null, AttachCard=()=>null, ExecutionStrip=({children,streaming,status})=>createElement("section",{"data-active":String(!!streaming)},status,children), livePhrase=()=>"", summarizeToolGroup=()=>({label:"",failed:0,added:null,removed:null}), FunctionStep=()=>null, ProviderSearchStep=()=>null, StepRow=()=>null, SubAgentStep=()=>null, SubAgentStrip=()=>null, StepExpansionScope=({children})=>children, RestartStep=()=>null, toolOutcome=b=>b.outcome, ThinkingStep=({text})=>text, MessageActions=()=>null, MessageTimestamp=()=>null, RuntimeBlock=()=>null, TurnFilesChips=()=>null;'
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

test('a failed assistant retains the streamed text alongside the error notice', () => {
  globalThis.approvalState={currentSessionId:'s1',pendingDecisions:[],executionUpdateOrders:{}};
  const html = renderToStaticMarkup(createElement(AssistantBubble,{msg:{
    id:'failed', role:'assistant',rowKind:'assistant', status:'error', content:'provider disconnected',
    blocks:[{type:'thinking',text:'Checking evidence'},{type:'text',text:'Partial response'}],
  },sessionIdOverride:'s1'}));
  assert.match(html, /Checking evidence/);
  assert.match(html, /Partial response/);
  assert.match(html, /provider disconnected/);
});

function renderBlocks(blocks, status='running', usage) {
  globalThis.approvalState={currentSessionId:'s1',pendingDecisions:[],executionUpdateOrders:{}};
  return renderToStaticMarkup(createElement(AssistantBubble,{msg:{id:'m1',role:'assistant',rowKind:'assistant',status,blocks,usage},sessionIdOverride:'s1'}));
}
test('only the final active execution segment animates and text ends that animation', () => {
  const blocks=[{type:'thinking',text:'Earlier'},{type:'text',text:'Update'},{type:'thinking',text:'Current'}];
  assert.deepEqual([...renderBlocks(blocks).matchAll(/data-active="(true|false)"/g)].map(m=>m[1]),['false','true']);
  assert.doesNotMatch(renderBlocks([...blocks,{type:'text',text:'Answer'}]), /data-active="true"/);
  assert.doesNotMatch(renderBlocks(blocks,'done'), /data-active="true"/);
});
test('assistant replies do not display the usage footer', () => {
  assert.doesNotMatch(renderBlocks([{type:'text',text:'Answer'}],'done',{input_tokens:89000,output_tokens:327,service_tiers:['priority']}), /runtime-usage-footer|Fast served|89.0k/);
});

test('only the original empty reply placeholder has the new logo', () => {
  assert.match(renderBlocks([]), /class="thinking-status"/);
  assert.doesNotMatch(renderBlocks([{type:'thinking',text:'Current'}]), /class="thinking-status"/);
  assert.doesNotMatch(renderBlocks([{type:'text',text:'Answer'}]), /class="thinking-status"/);
  globalThis.approvalState={currentSessionId:'s1',pendingDecisions:[],executionUpdateOrders:{}};
  const activeTool=renderToStaticMarkup(createElement(AssistantBubble,{msg:{
    id:'m1',role:'assistant',rowKind:'assistant',status:'running',content:'Update',
    tools:[{id:'t1',tool:'search',status:'running'}],
    blocks:[{type:'text',text:'Update'},{type:'tool',tool:'search',tool_call_id:'t1'}],
  }}));
  assert.doesNotMatch(activeTool, /class="thinking-status"/);
  for (const status of ['done','error','cancelled','interrupted']) {
    assert.doesNotMatch(renderBlocks([{type:'thinking',text:'Current'}],status), /class="thinking-status"/);
  }
  assert.doesNotMatch(render([decision],{e1:order}), /class="thinking-status"/);
});

test('live tools suppress motion alongside text and retry keeps its status label', () => {
  globalThis.approvalState={currentSessionId:'s1',pendingDecisions:[],executionUpdateOrders:{}};
  const msg={id:'m1',role:'assistant',rowKind:'assistant',status:'running',content:'Update',
    tools:[{id:'t1',tool:'search',status:'running'}],
    blocks:[{type:'tool',tool:'search',tool_call_id:'t1'},{type:'text',text:'Update'}]};
  const html=renderToStaticMarkup(createElement(AssistantBubble,{msg}));
  assert.doesNotMatch(html, /class="thinking-status"/);
  assert.doesNotMatch(html, /class="thinking-status"/);
  const parallelThinking=renderToStaticMarkup(createElement(AssistantBubble,{msg:{...msg,
    blocks:[...msg.blocks,{type:'thinking',text:'Current'}]}}));
  assert.equal([...parallelThinking.matchAll(/data-active="true"/g)].length,1);
  // The reply ends in text: the retry is a row of its own after it.
  const retry=renderToStaticMarkup(createElement(AssistantBubble,{msg:{...msg,retryStatus:{attempt:2,reason:'transport'}}}));
  assert.match(retry,/class="retry-strip">transport/);
  assert.doesNotMatch(retry,/retry-tag|retry-step/);
  assert.doesNotMatch(retry,/class="thinking-status"/);
  // The reply ends in a tool group: its row carries the tag and its
  // timeline ends with the retry row; no row of its own.
  const inGroup=renderToStaticMarkup(createElement(AssistantBubble,{msg:{...msg,content:'',
    blocks:[{type:'tool',tool:'search',tool_call_id:'t1'}],retryStatus:{attempt:3,reason:'network_wait',delayMs:20000}}}));
  assert.match(inGroup,/<section data-active="false"><span class="retry-tag">network_wait<\/span><span class="retry-step">network_wait<\/span><\/section>/);
  assert.doesNotMatch(inGroup,/retry-strip/);
  // A finished reply shows no retry.
  assert.doesNotMatch(renderToStaticMarkup(createElement(AssistantBubble,{msg:{...msg,status:'cancelled',retryStatus:{attempt:2,reason:'transport'}}})),/retry-/);
  const modelRetry=renderToStaticMarkup(createElement(AssistantBubble,{msg:{id:"m1",role:"assistant",status:"running",retryStatus:{attempt:2}}}));
  assert.doesNotMatch(modelRetry,/class="thinking-status"/);
  assert.match(modelRetry,/class="retry-strip"/);
  assert.doesNotMatch(renderToStaticMarkup(createElement(AssistantBubble,{msg:{...msg,status:'cancelled'}})),/class="thinking-status"/);
});

for (const [status,label] of [['cancelled','You stopped this reply'],['interrupted','This reply was interrupted']]) {
  test(`${status} empty and partial replies show terminal status`, () => {
    for (const blocks of [[],[{type:'text',text:'Saved partial reply'}]]) {
      const html=renderBlocks(blocks,status);
      assert.match(html, new RegExp(label));
      assert.doesNotMatch(html, /data-active="true"|typing-indicator/);
      if (blocks.length) assert.match(html,/Saved partial reply/);
    }
    assert.doesNotMatch(renderBlocks([],'done'),new RegExp(label));
  });
}

test('a split pane keeps its own sub-agent cards in its timeline, whatever session has focus', () => {
  globalThis.approvalState={currentSessionId:'focused',pendingDecisions:[],executionUpdateOrders:{}};
  // The server decides which cards are this conversation's (transcript/rows.py).
  const card=(session)=>({id:'card1',role:'assistant',rowKind:session==='peer'?'sub_agent_step':'external_reference',content:'',attach:{session_id:session,label:'Helper',status:'running'}});
  const draw=(session)=>renderToStaticMarkup(createElement(AssistantBubble,{msg:{
    id:'m1',role:'assistant',rowKind:'assistant',status:'done',blocks:[{type:'text',text:'Done'}],attachCards:[card(session)],
  },sessionIdOverride:'peer'}));
  assert.doesNotMatch(draw('peer'), /attach-row/);
  assert.match(draw('other'), /attach-row/);
});
