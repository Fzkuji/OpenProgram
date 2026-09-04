import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = ts.createSourceFile('steps.tsx', readFileSync(new URL('../components/chat/messages/execution-strip.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let callback;
function visit(node) {
  if(ts.isCallExpression(node) && node.expression.getText(source)==='useEffect' && node.arguments[0]?.getText(source).includes('store.detailNode?.path')) callback=node.arguments[0];
  ts.forEachChild(node,visit);
}
visit(source);

test('selected detail follows updates without opening or switching the dock',()=>{
  assert.ok(callback);
  const detail={path:'call1',status:'completed',output:'answer'};
  const store={detailNode:{path:'call1',status:'running'},populateDetail:n=>{store.detailNode=n;},showDetail:()=>assert.fail('must not open dock')};
  const run=()=>vm.runInNewContext(`(${callback.getText(source)})()`,{detail,onOpenDetail:undefined,useSessionStore:{getState:()=>store}});
  run();
  assert.deepEqual(store.detailNode,detail);
  store.detailNode={path:'other',status:'running'};
  run();
  assert.equal(store.detailNode.path,'other');
});
