import assert from 'node:assert/strict';
import test from "node:test";
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { presentTool } from '../../../components/chat/messages/execution/tool-presentation.ts';
import { guiToolView, guiStatus, guiResultNote } from '../../../components/chat/messages/execution/gui-tool-view.ts';
import { summarizeToolGroup } from '../../../components/chat/messages/execution/tool-group-summary.ts';

import { scratchDir } from "../../scratch-dir.mjs";

const web = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const dir = scratchDir(web, "gui-detail");
const file = join(dir, 'detail.mjs');
await build({
  absWorkingDir: web, entryPoints: ['components/right-sidebar/detail-panel.tsx'],
  bundle: true, platform: 'node', format: 'esm', packages: 'external', jsx: 'automatic', outfile: file,
  loader: { '.css': 'empty' },
  plugins: [{ name: 'detail-services', setup(b) {
    b.onResolve({ filter: /session-store$|i18n$|\/markdown$|llm-node-content$/ }, a => ({ path: a.path, namespace: 'stub' }));
    b.onLoad({ filter: /.*/, namespace: 'stub' }, a => ({ resolveDir: web, contents:
      a.path.includes('session-store') ? 'export const useSessionStore = s => s(globalThis.guiDetailState);' :
      a.path.endsWith('i18n') ? 'export const useTranslation=()=>({text:(en)=>en,t:k=>k});' :
      a.path.endsWith('markdown') ? 'export const useMarkdownReady=()=>{}, renderMarkdown=t=>t;' :
      'export const LlmNodeContent=()=>null;'
    }));
  }}],
});
const { DetailPanel } = await import(pathToFileURL(file));
const en = s => s;
const output = {
  status: 'infeasible', blocker: 'Window closed', handoff_instruction: 'Reopen TextEdit',
  history: [{type: 'capability_call', step: 1, capability: 'computer_use', input: {app_name: 'TextEdit'},
    output: {status: 'applied', success: true, step: {plan: {call: 'window_press'}, screenshot_artifact: '/tmp/recorded.png'}}}],
};
function render(value = output, extra = {}) {
  globalThis.guiDetailState = { currentSessionId: 'unrelated', detailNode: {
    path: 'gui-call', name: 'gui_agent', status: 'completed', session_id: 'owner',
    output: JSON.stringify(value), ...extra,
  }};
  return renderToStaticMarkup(createElement(DetailPanel));
}

test('GUI calls identify the task in the public tool presentation', () => {
  assert.equal(presentTool('gui_agent', JSON.stringify({task: 'Inspect TextEdit'}), en).title, 'GUI Agent');
  assert.equal(presentTool('gui_agent', JSON.stringify({task: 'Inspect TextEdit'}), en).target, 'Inspect TextEdit');
});

test('Details renders recorded GUI steps and handoff in place of the transport status', () => {
  const html = render();
  assert.match(html, /aria-label="GUI activity"/);
  assert.match(html, /Needs takeover/);
  assert.match(html, /Reopen TextEdit/);
  assert.match(html, /Computer Use/);
  assert.doesNotMatch(html, /detail-badge/, 'a completed transport status would contradict the GUI outcome');
  assert.doesNotMatch(html, /<img|<button/);
  assert.match(render(output, {name: 'custom_tool'}), /detail-badge/);
});

test('a summary that repeats the handoff is shown once', () => {
  const html = render({status: 'infeasible', summary: 'Open Photos', handoff_instruction: 'Open Photos', blocker: 'No window'});
  const card = html.slice(html.indexOf('aria-label="GUI activity"'), html.indexOf('</section>'));
  assert.equal(card.split('Open Photos').length - 1, 1);
  assert.match(html, /No operation steps were run/);
});

test('failed steps carry their reason; applied steps stay one line', () => {
  const view = guiToolView({name: 'gui_agent', status: 'completed', output: {history: [
    {type: 'capability_call', capability: 'browser_use', input: {url: 'https://a.test'}, output: {status: 'failed', summary: 'Page is private'}},
    {type: 'capability_call', capability: 'computer_use', input: {app_name: 'Photos'}, output: {status: 'applied', success: true, summary: 'Pressed'}},
  ]}});
  assert.deepEqual(view.steps.map(s => [s.target, s.status, s.reason]),
    [['https://a.test', 'failed', 'Page is private'], ['Photos', 'applied', '']]);
});

test('the timeline note uses the same status as Details', () => {
  assert.equal(guiResultNote('gui_agent', JSON.stringify({status: 'infeasible', handoff_instruction: 'Open Photos'}), 'completed', en),
    'Needs takeover · Open Photos');
  assert.equal(guiResultNote('gui_agent', '{"history":[', 'completed', en), 'Result unavailable');
});

test('history is ordered and takes precedence over duplicated capability children', () => {
  const history = ['computer_use', 'browser_use', 'vm_use'].map((capability, step) => ({
    type: 'capability_call', capability, step: step + 1, output: { status: 'applied', success: true },
  }));
  const view = guiToolView({ name: 'gui_agent', status: 'completed', output: {history},
    tree_root: {children: [{name: 'computer_use', output: {status: 'applied'}}]} });
  assert.deepEqual(view.steps.map(step => step.capability), ['computer_use', 'browser_use', 'vm_use']);
  assert.equal(view.status, 'unknown', 'completed function is not a verified task');
});

test('running calls use visible capability results without duplicating nested planners', () => {
  const view = guiToolView({ name: 'gui_agent', status: 'running', tree_root: {children: [
    { name: 'computer_use', status: 'completed', params: {app_name: 'TextEdit'},
      output: {status:'applied',success:true}, children:[{name:'computer_use'}] },
    { name: 'browser_use', status: 'running', params: {url: 'https://example.com'} },
    { name: 'vm_use', expose: 'hidden' },
  ]} });
  assert.equal(view.status, 'running');
  assert.deepEqual(view.steps.map(s => s.status), ['applied', 'running']);
  assert.equal(view.steps[0].target, 'TextEdit');
});

test('failure, cancellation and takeover do not become successful completion', () => {
  assert.equal(guiStatus({status: 'applied', success: false}), 'failed');
  assert.equal(guiStatus({status: 'succeeded'}, 'cancelled'), 'cancelled');
  assert.equal(guiStatus({status: 'infeasible', success: false}), 'infeasible');
  assert.equal(guiStatus({success: true}, 'completed'), 'unknown');
  const view = guiToolView({name:'computer_use',status:'completed',output:{status:'applied',step:{exec_result:{success:false,error:'Control unavailable'}}}});
  assert.equal(view.status, 'failed');
  assert.equal(view.summary, 'Control unavailable');
  assert.equal(view.steps.length, 0, 'a direct call is its own header, not a one-item list');
});

test('malformed, truncated and unrelated results keep raw details without false steps', () => {
  assert.equal(guiToolView({name:'custom_tool',status:'completed',output}), null);
  for (const output of ['{"history":[', 'plain text', 'null', '[]', undefined]) {
    const view = guiToolView({name:'gui_agent',status:'completed',output});
    assert.equal(view.status, 'unknown');
    assert.equal(view.steps.length, 0);
  }
  assert.doesNotMatch(render(output, {name:'custom_tool'}), /aria-label="GUI activity"/);
});

test('result text is rendered as text', () => {
  assert.doesNotMatch(render({...output,blocker:'<script>alert(1)</script>'}), /<script>/);
});

test('GUI activity participates in collapsed conversation summaries', () => {
  const block = {type:'tool',tool:'gui_agent',tool_call_id:'g1'};
  assert.equal(summarizeToolGroup([block],{text:en}).label,'Ran a GUI call');
  assert.equal(summarizeToolGroup([block],{text:en,active:true,runningIds:new Set(['g1'])}).label,'Running a GUI call…');
});

test('the public computer_use tool is not a GUI Agent step', () => {
  const params = {action: 'click', app: 'Calculator', element_index: 20};
  const output = 'Done: pressed button "7".\n\nApp: Calculator';
  assert.equal(guiToolView({name: 'computer_use', status: 'completed', output, params}), null);
  assert.equal(guiResultNote('computer_use', output, 'completed', (e) => e, params), '');
  assert.notEqual(guiToolView({name: 'computer_use', status: 'completed', output: {status: 'applied'},
    params: {task: 'inspect', app_name: 'TextEdit'}}), null);
});
