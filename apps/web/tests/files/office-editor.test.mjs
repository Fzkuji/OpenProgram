import test from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';

const moduleUrl = `/api/documents/office-module/${'a'.repeat(64)}.js`;
registerHooks({ resolve(specifier, context, next) {
  if (specifier === moduleUrl) return {url: 'data:text/javascript,' + encodeURIComponent(`
    export const mountOfficeEditor = (...args) => globalThis.officeFixture.mount(...args);
    export const createOfficeEditor = (...args) => globalThis.officeFixture.mount(...args).activate();
  `), shortCircuit: true};
  return next(specifier, context);
} });
const {createBoundOfficeEditor} = await import('../../lib/documents/office-editor.ts');
function fixture(status = 'ready') {
  let resolveActivation, started;
  const mounted = new Promise(resolve => {started = resolve;});
  const activation = new Promise(resolve => {resolveActivation = resolve;});
  const state = {destroyed: 0, attached: 0};
  const editor = {getState: () => ({status}), destroy: async () => {state.destroyed++;}};
  globalThis.officeFixture = {mount() {started(); return {activate: () => activation, destroy: editor.destroy};}};
  const options = {container: {}, bytes: new Blob(['pptx']), fileName:'slides.pptx', moduleUrl,
    hostUrl:'http://host-test.office.localhost:18100/office-host.html',
    controller:{getState:()=>({editorRevision:0}),attachRichEditor:()=>{state.attached++;return ()=>{};}}};
  return {options,state,mounted,finish:()=>resolveActivation(editor)};
}

test('closing a document destroys its pending Office host before activation finishes', async () => {
  const f=fixture(), abort=new AbortController();
  const pending=createBoundOfficeEditor({...f.options,signal:abort.signal});
  void pending.catch(()=>{});
  await f.mounted;
  abort.abort();
  try {
    assert.equal(f.state.destroyed,1,'pending mount must stop immediately');
    await assert.rejects(pending,{name:'AbortError'});
    assert.equal(f.state.attached,0);
  } finally {f.finish(); await pending.catch(()=>{});delete globalThis.officeFixture;}
});

test('startup deadline covers activation and never attaches a timed-out editor', async t => {
  t.mock.timers.enable({apis:['setTimeout']});
  const f=fixture();
  const pending=createBoundOfficeEditor(f.options);
  void pending.catch(()=>{});
  await f.mounted;
  t.mock.timers.tick(45000);
  try {
    assert.equal(f.state.destroyed,1,'deadline must destroy a pending mount');
    await assert.rejects(pending,/did not finish loading/);
    assert.equal(f.state.attached,0);
  } finally {f.finish();await pending.catch(()=>{});delete globalThis.officeFixture;}
});


test('host activation alone does not publish an opening native document', async () => {
  let options, instance;
  const f=fixture();
  globalThis.officeFixture.mount=(_container, value)=>{
    options=value;
    instance={getState:()=>({status:'opening'}),destroy:async()=>{}};
    return {activate:async()=>instance,destroy:instance.destroy};
  };
  let published=false;
  const pending=createBoundOfficeEditor(f.options).then(value=>{published=true;return value;});
  while (!options) await Promise.resolve();
  for(let i=0;i<8;i++) await Promise.resolve();
  assert.equal(published,false);
  assert.equal(f.state.attached,0);
  options.onReady();
  assert.equal(await pending,instance);
  assert.equal(f.state.attached,1);
  delete globalThis.officeFixture;
});

for (const reason of ['abort', 'timeout']) {
  test(`native readiness remains cancellable by ${reason} after activation`, async t => {
    t.mock.timers.enable({apis:['setTimeout']});
    const f=fixture('opening'), abort=new AbortController();
    const pending=createBoundOfficeEditor({...f.options,signal:abort.signal});
    void pending.catch(()=>{});
    await f.mounted;
    f.finish();
    for(let i=0;i<8;i++) await Promise.resolve();
    if(reason === 'abort') abort.abort();
    else t.mock.timers.tick(45000);
    await assert.rejects(pending, reason === 'abort' ? {name:'AbortError'} : /did not finish loading/);
    assert.equal(f.state.destroyed,1);
    assert.equal(f.state.attached,0);
    delete globalThis.officeFixture;
  });
}
