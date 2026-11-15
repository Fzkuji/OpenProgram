import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = ts.createSourceFile('steps.tsx', readFileSync(new URL('../../components/chat/messages/execution/execution-strip.tsx',import.meta.url),'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
let callback, sameDetail;
function visit(node) {
  if(ts.isCallExpression(node) && node.expression.getText(source)==='useEffect' && node.arguments[0]?.getText(source).includes('store.populateDetail(detail)')) callback=node.arguments[0];
  if(ts.isFunctionDeclaration(node) && node.name?.text==='sameDetail') sameDetail=vm.runInNewContext(`(${ts.transpile(node.getText(source),{target:ts.ScriptTarget.ES2020})})`);
  ts.forEachChild(node,visit);
}
visit(source);

test('selected detail follows updates without opening or switching the dock',()=>{
  assert.ok(callback);
  const detail={path:'call1',status:'completed',output:'answer'};
  const store={detailNode:{path:'call1',status:'running'},populateDetail:n=>{store.detailNode=n;},showDetail:()=>assert.fail('must not open dock')};
  const run=()=>vm.runInNewContext(`(${callback.getText(source)})()`,{detail,sameDetail,onOpenDetail:undefined,useSessionStore:{getState:()=>store}});
  run();
  assert.deepEqual(store.detailNode,detail);
  store.detailNode={path:'other',status:'running'};
  run();
  assert.equal(store.detailNode.path,'other');
});

test('custom tool details follow updates only after the full-output request settles',()=>{
  const detail={path:'gui1',status:'completed',output:'{"status":"infeasible"}',session_id:'owner'};
  const store={detailNode:{path:'gui1',status:'running'},populateDetail:n=>{store.detailNode=n;},showDetail:()=>assert.fail('must not open dock')};
  const run=syncDetail=>vm.runInNewContext(`(${callback.getText(source)})()`,{detail,sameDetail,onOpenDetail:()=>{},syncDetail,useSessionStore:{getState:()=>store}});
  run(false);
  assert.equal(store.detailNode.status,'running');
  run(true);
  assert.deepEqual(store.detailNode,detail);
  store.detailNode={path:'other'};
  run(true);
  assert.equal(store.detailNode.path,'other');
});

test('selected detail never adopts the same path from another conversation',()=>{
  const detail={path:'chat-tool:t1',status:'completed',session_id:'a'};
  const store={detailNode:{path:'chat-tool:t1',status:'running',session_id:'b'},populateDetail:n=>{store.detailNode=n;}};
  vm.runInNewContext(`(${callback.getText(source)})()`,{detail,sameDetail,onOpenDetail:undefined,useSessionStore:{getState:()=>store}});
  assert.equal(store.detailNode.session_id,'b');
});
