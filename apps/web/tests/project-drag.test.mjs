import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";
const { window } = parseHTML('<html><body></body></html>');
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { useProjectDrag } = await import('../components/sidebar/sessions-list/use-project-drag.ts');
const { moveProject } = await import('../lib/project-groups.ts');

test('pointer drag commits on release, cancels safely, and leaves normal clicks intact', async () => {
  let order = ['a', 'b', 'c'];
  let clicks = 0;
  let saves = 0;
  let last;
  const frames = new Map(); let frameId = 0;
  window.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  window.cancelAnimationFrame = id => frames.delete(id);
  function Harness({ enabled = true }) {
    last = useProjectDrag(enabled, (source, target, side) => {
      order = moveProject(order, source, target, side); saves++;
    });
    return h('div', { id: 'sidebar', className: 'overflow-y-auto' }, order.map(id => h('div', { key:id, 'data-project-id':id },
      h('div', { ...last.headerProps(id), role:'button', 'aria-keyshortcuts':'Alt+ArrowUp Alt+ArrowDown', onClick: event => { last.headerProps(id).onClick(event); if (!event.defaultPrevented) clicks++; } }, id, h('button', {}, '+')))));
  }
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(h(Harness)));
  const scroller = host.firstElementChild;
  scroller.scrollTop = 0;
  scroller.getBoundingClientRect = () => ({top:0,bottom:120,left:0,right:200});
  const header = id => host.querySelector(`[data-project-id="${id}"]`).firstElementChild;
  const capture = new Set();
  for (const id of order) {
    const el = header(id);
    el.setPointerCapture = p => capture.add(p);
    el.hasPointerCapture = p => capture.has(p);
    el.releasePointerCapture = p => capture.delete(p);
    el.getBoundingClientRect = () => ({top:order.indexOf(id)*40, height:32});
    el.parentElement.getBoundingClientRect = () => ({top:order.indexOf(id)*40, height:32});
  }
  document.elementFromPoint = (x, y) => x < 0 ? null : header(order[Math.floor(y/40)]);
  async function fire(id, type, x, y, extra = {}) {
    await act(async () => {
      const event = new window.Event(type, {bubbles:true, cancelable:true});
      Object.assign(event, {button:0, isPrimary:true, pointerId:1, clientX:x, clientY:y, detail:1}, extra);
      header(id).dispatchEvent(event);
    });
  }
  await fire('c','pointerdown',10,90);
  await fire('c','pointermove',10,2);
  assert.equal(saves,0); assert.equal(last.projectDrop.side,'before');
  assert.equal(last.draggingProject.id,'c');
  assert.equal(last.projectOffset('a'),40,'Neighbor previews displacement before saving');
  assert.equal(last.projectOffset('c'),-96,'Dragged project follows pointer and edge scroll');
  assert.equal(frames.size,1);
  await fire('c','pointerup',10,2);
  assert.deepEqual(order,['c','a','b']); assert.equal(saves,1);
  assert.equal(capture.size,0); assert.equal(last.draggingProject,null); assert.equal(frames.size,0);
  await fire('c','click',10,2); assert.equal(clicks,0);
  await fire('c','pointerdown',10,2);
  await fire('c','pointerup',10,3);
  await fire('c','click',10,3); assert.equal(clicks,1);
  await fire('c','pointerdown',10,2);
  await fire('c','pointermove',10,105);
  const priorScroll = scroller.scrollTop;
  const [pendingFrame, tick] = frames.entries().next().value;
  frames.delete(pendingFrame);
  await act(async () => tick());
  assert.ok(scroller.scrollTop > priorScroll);
  await fire('c','pointerup',10,105);
  assert.deepEqual(order,['a','b','c']); assert.equal(saves,2);
  for (const cancel of ['Escape','blur','pointercancel','lostpointercapture','outside','disabled']) {
    await fire('c','pointerdown',10,90);
    await fire('c','pointermove',10,2);
    if (cancel === 'outside') await fire('c','pointerup',-10,2);
    else if (cancel === 'disabled') await act(async () => root.render(h(Harness,{enabled:false})));
    else if (cancel === 'Escape' || cancel === 'blur') await act(async () => {
      const event = new window.Event(cancel === 'Escape' ? 'keydown' : 'blur');
      event.key = 'Escape'; window.dispatchEvent(event);
    });
    else await fire('c',cancel,10,2);
    assert.equal(saves,2, cancel); assert.equal(last.draggingProject,null,cancel);
    assert.equal(capture.size,0,cancel); assert.equal(frames.size,0,cancel);
    if (cancel === 'disabled') await act(async () => root.render(h(Harness)));
  }
  // Action buttons and secondary pointers never start a project drag.
  await act(async () => {
    const event = new window.Event('pointerdown',{bubbles:true});
    Object.assign(event,{button:0,isPrimary:true,pointerId:1,clientX:10,clientY:90});
    header('c').querySelector('button').dispatchEvent(event);
  });
  assert.equal(capture.size,0);
  await fire('c','pointerdown',10,90,{button:2}); assert.equal(capture.size,0);
  await fire('c','pointerdown',10,90);
  await fire('c','pointermove',10,2);
  await act(async () => root.unmount());
  assert.equal(capture.size,0);
  host.remove();
});


test('project order persists, reloads, synchronizes, and survives a storage error', async () => {
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value) };
  globalThis.localStorage = storage; window.localStorage = storage;
  const prefs = await import('../lib/prefs/recents-view.ts');
  let changes = 0;
  const unsubscribe = prefs.subscribeRecentsView(() => changes++);
  prefs.setRecentsView({projectOrder:['c','a','b'],projectSort:'oldest',pinnedProjects:['a'],sortDirection:'asc'});
  assert.deepEqual(JSON.parse(values.get('recents_view')).projectOrder,['c','a','b']);
  const reload = await import('../lib/prefs/recents-view.ts?reload');
  assert.deepEqual(reload.getRecentsView().projectOrder,['c','a','b']);
  assert.equal(reload.getRecentsView().projectSort,'oldest');
  assert.deepEqual(reload.getRecentsView().pinnedProjects,['a']);
  assert.equal(reload.getRecentsView().sortDirection,'asc');
  values.set('recents_view', JSON.stringify({projectOrder:['b','b','a']}));
  const event = new window.Event('storage'); event.key = 'recents_view'; window.dispatchEvent(event);
  assert.deepEqual(prefs.getRecentsView().projectOrder,['b','a']);
  values.set('recents_view', JSON.stringify({projectOrder:[null]})); window.dispatchEvent(event);
  assert.deepEqual(prefs.getRecentsView().projectOrder,[]);
  storage.setItem = () => { throw new Error('unavailable'); };
  assert.throws(() => prefs.setRecentsView({projectOrder:['a','c']}));
  assert.deepEqual(prefs.getRecentsView().projectOrder,['a','c']);
  assert.equal(changes,4);
  unsubscribe(); window.dispatchEvent(event); assert.equal(changes,4);
  values.set('recents_view', JSON.stringify({sort:'title',groupBy:'flat'}));
  const legacyTitle = await import('../lib/prefs/recents-view.ts?legacy-title');
  assert.equal(legacyTitle.getRecentsView().sortDirection,'asc');
  values.set('recents_view', JSON.stringify({sort:'title',sortDirection:'desc'}));
  const explicitTitle = await import('../lib/prefs/recents-view.ts?explicit-title');
  assert.equal(explicitTitle.getRecentsView().sortDirection,'desc');
  values.set('recents_view', JSON.stringify({sort:'recency'}));
  const legacyTime = await import('../lib/prefs/recents-view.ts?legacy-time');
  assert.equal(legacyTime.getRecentsView().sortDirection,'desc');
});
