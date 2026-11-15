import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import {
  PANE_MIN, dockLayout, leaves, presetLayout, resizeSplit, rowLayout, snapResize, tilePane, unitRects,
} from '../../lib/tabs/canvas/canvas-layout.ts';
import { rememberCanvas, takePreviousCanvas } from '../../lib/tabs/canvas/canvas-history.ts';
import { readGlobalStyles } from '../../scripts/testing/feature-source.mjs';

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), 'utf8');
const rectOf = (layout, content) => {
  const pane = leaves(layout.root).find(p => p.content === content);
  const r = unitRects(layout.root).get(pane.id);
  return [r.left, r.top, r.width, r.height].map(v => Math.round(v * 100) / 100);
};
const empties = (layout) => leaves(layout.root).filter(p => !p.content);

test('a corner drop on a lone tab takes a quarter and leaves an empty quarter to fill', () => {
  const next = dockLayout(rowLayout(['a']), ['b'], 'root', 'top-left');
  assert.deepEqual(rectOf(next, 'b'), [0, 0, 0.5, 0.5]);
  assert.deepEqual(rectOf(next, 'a'), [0.5, 0, 0.5, 1]);
  const [empty] = empties(next);
  assert.ok(empty, 'the other left quarter is an empty pane offering tabs');
  const r = unitRects(next.root).get(empty.id);
  assert.deepEqual([r.left, r.top, r.width, r.height], [0, 0.5, 0.5, 0.5]);
});

test('a corner drop splits the half that holds the corner into quarters', () => {
  const columns = dockLayout(rowLayout(['a', 'b']), ['c'], 'root', 'bottom-right');
  assert.deepEqual(rectOf(columns, 'c'), [0.5, 0.5, 0.5, 0.5]);
  assert.deepEqual(rectOf(columns, 'b'), [0.5, 0, 0.5, 0.5]);
  assert.deepEqual(rectOf(columns, 'a'), [0, 0, 0.5, 1]);
  assert.equal(empties(columns).length, 0);
  const rows = dockLayout(presetLayout('2x1', ['a', 'b']), ['c'], 'root', 'top-left');
  assert.deepEqual(rectOf(rows, 'c'), [0, 0, 0.5, 0.5]);
  assert.deepEqual(rectOf(rows, 'a'), [0.5, 0, 0.5, 0.5]);
});

test('edge drops take a half of the whole canvas and keep the rest arranged', () => {
  const next = dockLayout(rowLayout(['a', 'b']), ['c'], 'root', 'bottom');
  assert.deepEqual(rectOf(next, 'c'), [0, 0.5, 1, 0.5]);
  assert.deepEqual(rectOf(next, 'a'), [0, 0, 0.5, 0.5]);
});

test('dropping a pane on itself changes nothing', () => {
  const layout = rowLayout(['a', 'b']);
  const own = leaves(layout.root).find(p => p.content === 'a');
  assert.equal(dockLayout(layout, ['a'], own.id, 'left'), null);
});

test('Move and resize sends a pane to a half; the others keep their order in the rest', () => {
  const layout = presetLayout('main+2', ['a', 'b', 'c']);
  const c = leaves(layout.root).find(p => p.content === 'c');
  const next = tilePane(layout.root, c.id, 'left');
  assert.equal(next.paneId, c.id, 'the pane keeps its id so focus follows it');
  const tiled = { root: next.root, focusedPaneId: next.paneId };
  assert.deepEqual(rectOf(tiled, 'c'), [0, 0, 0.5, 1]);
  assert.ok(rectOf(tiled, 'a')[0] < rectOf(tiled, 'b')[0], 'a stays before b');
});

test('a quarter already taken trades places with the moving pane', () => {
  const layout = presetLayout('2x2', ['a', 'b', 'c', 'd']);
  const a = leaves(layout.root).find(p => p.content === 'a');
  const next = tilePane(layout.root, a.id, 'bottom-right');
  const tiled = { root: next.root, focusedPaneId: next.paneId };
  assert.deepEqual(rectOf(tiled, 'a'), [0.5, 0.5, 0.5, 0.5]);
  assert.deepEqual(rectOf(tiled, 'd'), [0, 0, 0.5, 0.5]);
  assert.equal(leaves(next.root).find(p => p.id === next.paneId).content, 'a', 'focus follows the moved tab');
});

test('a lone pane has nothing to tile against', () => {
  const layout = rowLayout(['a']);
  const next = tilePane(layout.root, leaves(layout.root)[0].id, 'left');
  assert.equal(next.root, layout.root);
});

test('dividers stick at a third, a half and two thirds', () => {
  assert.deepEqual(snapResize([0.5, 0.5], 0, 0.16, 600), { delta: 2 / 3 - 0.5, snap: 2 / 3 });
  assert.deepEqual(snapResize([0.4, 0.6], 0, 0.095, 600), { delta: 0.5 - 0.4, snap: 0.5 });
  assert.deepEqual(snapResize([0.5, 0.5], 0, 0.1, 600), { delta: 0.1, snap: null });
  // A snap point outside the two panes being resized is ignored.
  assert.equal(snapResize([0.2, 0.1, 0.7], 0, 0.125, 600).snap, null);
});

test('Return to previous layout finds a layout by any tab it involved', () => {
  const before = rowLayout(['x', 'y']);
  rememberCanvas(['g1', 'x', 'y'], before);
  assert.equal(takePreviousCanvas(['z']), undefined);
  assert.equal(takePreviousCanvas(['y']), before);
  assert.equal(takePreviousCanvas(['y']), undefined, 'each step is used once');
});

test('drags cannot hang: missed releases, errors and drag regions all end them', async () => {
  const drag = await read('lib/tabs/canvas/canvas-drag.ts');
  assert.match(drag, /event\.buttons !== 0/, 'the first buttonless move ends a drag whose release was lost');
  assert.match(drag, /function resolveDrop[\s\S]*?catch \(error\)/, 'a failed drop lookup cannot skip cleanup');
  assert.match(readGlobalStyles(), /html\[data-canvas-dragging\] \* \{ -webkit-app-region: no-drag !important; \}/, 'window drag regions never swallow a drag');
});

test('a divider stops at the panes\' minimum size and never squeezes a small pane further', () => {
  const layout = rowLayout(['a', 'b']);
  const span = 800;
  const widest = resizeSplit(layout.root, layout.root.id, 0, 0.9, span);
  assert.equal(Math.round(widest.sizes[1] * span), PANE_MIN.width, 'the right pane keeps its minimum width');
  const narrow = { ...layout.root, sizes: [0.2, 0.8] };
  const squeezed = resizeSplit(narrow, narrow.id, 0, -0.1, span);
  assert.equal(squeezed.sizes[0], 0.2, 'a pane already under the minimum does not shrink');
  const grown = resizeSplit(narrow, narrow.id, 0, 0.1, span);
  assert.ok(Math.abs(grown.sizes[0] - 0.3) < 1e-9, 'but it can grow');
});

test('a picked window zooms from its own place even as the cards reflow', async () => {
  // Picking another window resizes the center area, so every card reflows
  // as the zoom starts. A transform on the zooming card would make it the
  // picture's containing block, and the picture would grow from the
  // card's corner, off to the side.
  const view = await read('components/center-tabs/canvas/canvas-overview.tsx');
  assert.match(view, /layout=\{!landing && zoom\?\.key !== card\.key\}/);
  assert.match(view, /style=\{zoomed \? \{ position: "absolute", zIndex: 3, \.\.\.\(area\.canvasWidth > 0 \? \{ left: 0, top: 0,/);
});
