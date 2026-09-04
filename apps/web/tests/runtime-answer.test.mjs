import assert from 'node:assert/strict';
import test from 'node:test';
import { runtimeAnswer } from '../components/chat/messages/runtime-summary.ts';

test('direct GUI answer is full persisted prose and retains takeover instructions', () => {
  const summary = '屏幕显示浏览器设置页。'.repeat(20);
  assert.equal(runtimeAnswer({fnName:'gui_agent',status:'completed',tree:{output:JSON.stringify({status:'succeeded',summary})}}),summary);
  assert.equal(runtimeAnswer({fnName:'gui_agent',status:'completed',tree:{output:{status:'infeasible',summary:'无法操作',handoff_instruction:'请手动授权。'}}}),'无法操作\n\n请手动授权。');
});
test('partial results remain hidden; workflows keep their own conclusion', () => {
  assert.equal(runtimeAnswer({fnName:'gui_agent',status:'running',tree:{output:{summary:'partial'}}}),null);
  assert.equal(runtimeAnswer({fnName:'auto_workflow',status:'completed',tree:{output:{summary:'existing conclusion'}}}),null);
});
test('other direct functions retain text and structured results', () => {
  assert.equal(runtimeAnswer({fnName:'read_report',status:'completed',tree:{output:'Full answer'}}),'Full answer');
  assert.equal(runtimeAnswer({fnName:'count',status:'completed',tree:{output:0}}),'0');
  assert.match(runtimeAnswer({fnName:'extract',status:'completed',tree:{output:{rows:[1,2]}}}),/^```json/);
});
