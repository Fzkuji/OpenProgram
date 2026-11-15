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
const { useChatDrag } = await import('../../components/sidebar/sessions/use-chat-drag.ts');

test('a chat row slides with the pointer, the lists make room, and a drop names the project and the neighbours of the place; a cancel and a plain click do nothing', async () => {
  const groups = { a: ['s1', 's2'], b: ['s3'] };
  const moves = [];
  let clicks = 0;
  let last;
  const frames = new Map(); let frameId = 0;
  window.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
  window.cancelAnimationFrame = id => frames.delete(id);
  function Harness({ enabled = true, reorder = true }) {
    last = useChatDrag(enabled, reorder, (sessionId, projectId, before, after) => moves.push([sessionId, projectId, before, after]));
    return h('div', { id: 'sidebar', className: 'overflow-y-auto' }, Object.entries(groups).map(([project, chats]) =>
      h('div', { key: project, 'data-project-id': project, 'data-drop-target': last.dropProject === project || undefined },
        h('div', { className: 'header', 'data-project-header': '' }, project),
        ...chats.map(id => h('div', { key: id, id, ...last.rowProps(id, project), role: 'button',
          onClick: event => { last.rowProps(id, project).onClick(event); if (!event.defaultPrevented) clicks++; } },
          `Chat ${id}`, h('button', {}, '⋮'))))));
  }
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(h(Harness)));
  const scroller = host.firstElementChild;
  scroller.scrollTop = 0;
  scroller.getBoundingClientRect = () => ({ top: 0, bottom: 200, left: 0, right: 200 });
  // Project a fills y 0–100, project b y 100–160.
  host.querySelector('[data-project-id="a"]').getBoundingClientRect = () => ({ top: 0, height: 100 });
  host.querySelector('[data-project-id="b"]').getBoundingClientRect = () => ({ top: 100, height: 60 });
  // Headers and rows are 30px tall, one after another in each project.
  const boxes = { a: 0, s1: 30, s2: 60, b: 100, s3: 130 };
  for (const [project, top] of [['a', 0], ['b', 100]]) host.querySelector(`[data-project-id="${project}"] .header`).getBoundingClientRect = () => ({ top, height: 30 });
  for (const id of ['s1', 's2', 's3']) host.querySelector(`#${id}`).getBoundingClientRect = () => ({ top: boxes[id], height: 30 });
  const row = id => host.querySelector(`#${id}`);
  const capture = new Set();
  for (const id of ['s1', 's2', 's3']) {
    const el = row(id);
    el.setPointerCapture = p => capture.add(p);
    el.hasPointerCapture = p => capture.has(p);
    el.releasePointerCapture = p => capture.delete(p);
  }
  async function fire(id, type, x, y, extra = {}) {
    await act(async () => {
      const event = new window.Event(type, { bubbles: true, cancelable: true });
      Object.assign(event, { button: 0, isPrimary: true, pointerId: 1, clientX: x, clientY: y, detail: 1 }, extra);
      row(id).dispatchEvent(event);
    });
  }
  // Carried from a onto b: b lights up, release moves the chat, the click is swallowed.
  await fire('s1', 'pointerdown', 10, 40);
  await fire('s1', 'pointermove', 10, 42);
  assert.equal(last.draggingChat, null, 'a 2px wiggle is not a drag');
  await fire('s1', 'pointermove', 10, 120);
  assert.deepEqual(last.draggingChat, { id: 's1', dy: 80 });
  assert.equal(row('s1').style.transform, 'translateY(80px)', 'the row itself slides with the pointer');
  assert.ok(row('s1').hasAttribute('data-dragging'));
  assert.equal(document.querySelector('[class*=Ghost]'), null, 'no separate label follows the pointer');
  assert.equal(last.dropProject, 'b');
  assert.ok(host.querySelector('[data-project-id="b"]').hasAttribute('data-drop-target'));
  // The lists make room: everything between its old place and the
  // pointer (s2, then project b's header) moves up one row; s3, below
  // the pointer, stays, so the gap opens above it.
  assert.equal(last.rowOffset('s2'), -30); assert.equal(last.headerOffset('b'), -30);
  assert.deepEqual(last.dropGrow, { top: 30, bottom: 0 }, 'the frame round b reaches up over the gap its rows left');
  assert.equal(last.rowOffset('s3'), 0); assert.equal(last.headerOffset('a'), 0);
  assert.equal(row('s2').style.transform, 'translateY(-30px)');
  // Carried on past s3's middle (as it sits once s1 has left), the gap
  // opens after s3 instead; back up, before it again.
  await fire('s1', 'pointermove', 10, 140);
  assert.equal(last.rowOffset('s3'), -30); assert.equal(last.dropProject, 'b');
  await fire('s1', 'pointermove', 10, 120);
  assert.equal(last.rowOffset('s3'), 0);
  assert.equal(frames.size, 1, 'edge auto-scroll is armed while dragging');
  await fire('s1', 'pointerup', 10, 120);
  assert.deepEqual(moves, [['s1', 'b', null, 's3']], 'moved to b, before s3');
  assert.equal(last.draggingChat, null); assert.equal(last.dropProject, null);
  assert.equal(last.rowOffset('s2'), 0); assert.equal(last.headerOffset('b'), 0);
  assert.deepEqual(last.dropGrow, { top: 0, bottom: 0 });
  assert.equal(capture.size, 0); assert.equal(frames.size, 0);
  await fire('s1', 'click', 10, 120); assert.equal(clicks, 0);
  // A plain press and release still opens the chat.
  await fire('s1', 'pointerdown', 10, 40);
  await fire('s1', 'pointerup', 10, 41);
  await fire('s1', 'click', 10, 41); assert.equal(clicks, 1);
  // Inside its own project a drag reorders: carried above s1, s1 moves
  // down (nothing lights up) and the release places s2 before it.
  await fire('s2', 'pointerdown', 10, 60);
  await fire('s2', 'pointermove', 10, 20);
  assert.equal(last.dropProject, null);
  assert.equal(last.rowOffset('s1'), 31); assert.equal(last.headerOffset('b'), 0);
  assert.deepEqual(last.dropGrow, { top: 0, bottom: 0 });
  await fire('s2', 'pointerup', 10, 20);
  assert.deepEqual(moves[1], ['s2', 'a', null, 's1']);
  // Let go where it already was, nothing happens.
  await fire('s2', 'pointerdown', 10, 60);
  await fire('s2', 'pointermove', 10, 66);
  assert.equal(last.rowOffset('s1'), 0);
  await fire('s2', 'pointerup', 10, 66);
  assert.equal(moves.length, 2);
  // With reordering off (the lists not sorted by time), its own project is no place at all.
  await act(async () => root.render(h(Harness, { reorder: false })));
  await fire('s2', 'pointerdown', 10, 60);
  await fire('s2', 'pointermove', 10, 20);
  assert.equal(last.rowOffset('s1'), 0);
  await fire('s2', 'pointerup', 10, 20);
  assert.equal(moves.length, 2);
  await act(async () => root.render(h(Harness)));
  // Carried up to just past project b's header (its own project's), it
  // would join the end of project a: only b's header moves down, and
  // a's frame reaches down over that gap. Released outside, nothing moves.
  await fire('s3', 'pointerdown', 10, 130);
  await fire('s3', 'pointermove', 10, 100);
  assert.equal(last.dropProject, 'a');
  assert.equal(last.headerOffset('b'), 31); assert.equal(last.rowOffset('s2'), 0); assert.equal(last.rowOffset('s1'), 0);
  assert.deepEqual(last.dropGrow, { top: 0, bottom: 31 });
  // A little lower its centre is still over b's own header: no target.
  await fire('s3', 'pointermove', 10, 118);
  assert.equal(last.dropProject, null); assert.equal(last.headerOffset('b'), 0);
  await fire('s3', 'pointerup', 300, 118);
  assert.equal(moves.length, 2);
  for (const cancel of ['Escape', 'blur', 'pointercancel', 'lostpointercapture', 'outside', 'disabled']) {
    await fire('s3', 'pointerdown', 10, 130);
    await fire('s3', 'pointermove', 10, 40);
    assert.equal(last.dropProject, 'a', cancel);
    // Carried up over s1: s1, s2 and project b's header move down one
    // row (s3's height plus the 1px gap) to open the gap above s1.
    assert.equal(last.rowOffset('s1'), 31, cancel); assert.equal(last.rowOffset('s2'), 31, cancel);
    assert.equal(last.headerOffset('b'), 31, cancel); assert.equal(last.headerOffset('a'), 0, cancel);
    assert.deepEqual(last.dropGrow, { top: 0, bottom: 31 }, cancel);
    if (cancel === 'outside') await fire('s3', 'pointerup', 300, 40);
    else if (cancel === 'disabled') await act(async () => root.render(h(Harness, { enabled: false })));
    else if (cancel === 'Escape' || cancel === 'blur') await act(async () => {
      const event = new window.Event(cancel === 'Escape' ? 'keydown' : 'blur');
      event.key = 'Escape'; window.dispatchEvent(event);
    });
    else await fire('s3', cancel, 10, 40);
    assert.equal(moves.length, 2, cancel); assert.equal(last.draggingChat, null, cancel);
    assert.equal(last.rowOffset('s1'), 0, cancel); assert.equal(last.headerOffset('b'), 0, cancel);
    assert.equal(capture.size, 0, cancel); assert.equal(frames.size, 0, cancel);
    if (cancel === 'disabled') await act(async () => root.render(h(Harness)));
  }
  // The row's own button and a secondary pointer never start a drag.
  await act(async () => {
    const event = new window.Event('pointerdown', { bubbles: true });
    Object.assign(event, { button: 0, isPrimary: true, pointerId: 1, clientX: 10, clientY: 40 });
    row('s1').querySelector('button').dispatchEvent(event);
  });
  assert.equal(capture.size, 0);
  await fire('s1', 'pointerdown', 10, 40, { button: 2 }); assert.equal(capture.size, 0);
  await act(async () => root.unmount());
  host.remove();
});
