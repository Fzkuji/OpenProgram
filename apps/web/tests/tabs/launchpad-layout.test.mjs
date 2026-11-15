import assert from 'node:assert/strict';
import test from 'node:test';
import {
  PAGE_CAPACITY, EMPTY_LAYOUT, reconcile, moveEntry, mergeInto, moveWithinFolder, renameFolder,
  parseLayout, placedIds, findFolderOf,
} from '../../lib/tabs/launchpad/launchpad-layout.ts';

const ids = n => Array.from({ length: n }, (_, i) => `app:${i}`);
const pageIds = layout => layout.pages.map(page => page.map(entry => entry.id));

test('new ids fill the last page and spill onto new pages at capacity', () => {
  const layout = reconcile(EMPTY_LAYOUT, ids(PAGE_CAPACITY + 3));
  assert.equal(layout.pages.length, 2);
  assert.equal(layout.pages[0].length, PAGE_CAPACITY);
  assert.deepEqual(pageIds(layout)[1], ['app:35', 'app:36', 'app:37']);
});

test('reconcile keeps the saved arrangement, drops removed ids and appends new ones', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b', 'c', 'd']);
  layout = mergeInto(layout, 'c', 'a', 'folder:1');
  layout = reconcile(layout, ['b', 'c', 'd', 'e']);
  // 'a' is gone, so the folder holds only 'c' and dissolves into it in place.
  assert.deepEqual(pageIds(layout), [['c', 'b', 'd', 'e']]);
});

test('an item dropped on another makes a folder in the target place', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b', 'c']);
  layout = mergeInto(layout, 'c', 'a', 'folder:1');
  assert.deepEqual(layout.pages[0][0], { kind: 'folder', id: 'folder:1', name: '', items: ['a', 'c'] });
  assert.deepEqual(pageIds(layout), [['folder:1', 'b']]);
  layout = mergeInto(layout, 'b', 'folder:1', 'folder:2');
  assert.deepEqual(layout.pages[0], [{ kind: 'folder', id: 'folder:1', name: '', items: ['a', 'c', 'b'] }]);
});

test('a folder is never dropped into another folder or item', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b', 'c', 'd']);
  layout = mergeInto(layout, 'b', 'a', 'folder:1');
  const before = layout;
  assert.equal(mergeInto(layout, 'folder:1', 'c', 'folder:2'), before);
});

test('moving an item out of a folder of two dissolves the folder in place', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b', 'c']);
  layout = mergeInto(layout, 'b', 'a', 'folder:1');
  layout = moveEntry(layout, 'b', 0, 2);
  assert.deepEqual(pageIds(layout), [['a', 'c', 'b']]);
  assert.equal(findFolderOf(layout, 'a'), null);
});

test('inserting into a full page pushes its last entry onto the next page', () => {
  let layout = reconcile(EMPTY_LAYOUT, ids(PAGE_CAPACITY + 1));
  layout = moveEntry(layout, 'app:35', 0, 0);
  assert.equal(pageIds(layout)[0][0], 'app:35');
  assert.deepEqual(pageIds(layout)[1], ['app:34']);
});

test('dragging past the last page opens a new page; emptying a page removes it', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b']);
  layout = moveEntry(layout, 'b', 1, 0);
  assert.deepEqual(pageIds(layout), [['a'], ['b']]);
  layout = moveEntry(layout, 'b', 0, 0);
  assert.deepEqual(pageIds(layout), [['b', 'a']]);
});

test('reordering inside a folder, renaming, and a stored round trip', () => {
  let layout = reconcile(EMPTY_LAYOUT, ['a', 'b', 'c']);
  layout = mergeInto(layout, 'b', 'a', 'folder:1');
  layout = mergeInto(layout, 'c', 'folder:1', 'folder:2');
  layout = moveWithinFolder(layout, 'folder:1', 'c', 0);
  layout = renameFolder(layout, 'folder:1', '  Tools ');
  assert.deepEqual(layout.pages[0][0], { kind: 'folder', id: 'folder:1', name: 'Tools', items: ['c', 'a', 'b'] });
  assert.deepEqual(parseLayout(JSON.parse(JSON.stringify(layout))), layout);
  assert.deepEqual(placedIds(layout).sort(), ['a', 'b', 'c']);
});

test('a malformed stored layout falls back to an empty one', () => {
  assert.deepEqual(parseLayout(null), EMPTY_LAYOUT);
  assert.deepEqual(parseLayout({ pages: [[{ kind: 'item' }, 3, { kind: 'item', id: 'a' }]] }), { pages: [[{ kind: 'item', id: 'a' }]] });
});
