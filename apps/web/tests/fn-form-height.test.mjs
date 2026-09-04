import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Exercise the production DOM measurement without importing React's hook runtime.
const source = ts.createSourceFile('height.ts', readFileSync(new URL('../components/chat/composer/modes/fn-form/use-fn-form-wrapper.ts', import.meta.url),'utf8'), ts.ScriptTarget.Latest, true);
const code = source.statements.filter(n => ts.isFunctionDeclaration(n) && ['formChromeHeight','labelLineHeight'].includes(n.name?.text)).map(n=>n.getText(source)).join('\n');

test('closed Advanced labels do not enlarge an empty primary field', () => {
  const label = closed => ({scrollHeight:21,parentElement:{},closest:()=>closed ? {} : null});
  const labels = [label(false),...Array.from({length:6},()=>label(true))];
  const body = {querySelector:()=>labels[0],querySelectorAll:s=>s==='details > summary' ? [{offsetHeight:32}] : labels};
  const header = {offsetHeight:48};
  const el = {querySelector:s=>s==='[data-fn-form-header]' ? header : body};
  const measure = vm.runInNewContext(ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText+'\nformChromeHeight',{
    getComputedStyle:n=>n===el ? {paddingBottom:'0'} : {paddingTop:'12',paddingBottom:'12',gap:n===body?'12':'6',fontSize:'14',lineHeight:'21'},
  });
  assert.equal(measure(el),143);
});
