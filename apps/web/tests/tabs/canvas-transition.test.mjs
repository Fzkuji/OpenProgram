import assert from 'node:assert/strict';
import test from "node:test";
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

import { scratchDir } from "../scratch-dir.mjs";

const root = dirname(fileURLToPath(new URL('../../package.json', import.meta.url)));
const require = createRequire(join(root, 'package.json'));
const {parseHTML} = require('linkedom');
const dir = scratchDir(root, "canvas-transition");
await build({absWorkingDir: root, stdin: {contents: `
export {captureCanvasTransition, playCanvasTransition, prepareCanvasFold} from './lib/tabs/canvas/canvas-transition';
export {canvasPanesMoving} from './lib/tabs/canvas/canvas-motion';
`, resolveDir: root}, bundle: true, format: 'esm', platform: 'node', packages: 'external',
  tsconfig: join(root, 'tsconfig.json'), outfile: join(dir, 'probe.mjs'), loader: {'.css': 'empty'}});

const {window} = parseHTML('<html><body><div data-canvas-root><div id="chatArea-shell" data-center-tab-id="a"><div id="chatArea" class="chat-area">A</div></div><div data-center-tab-id="b">B</div></div></body></html>');
const document = window.document;
const rects = new Map();
const animations = [];
const store = new Map();
const storage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)};
Object.assign(globalThis, {window, document, localStorage: storage, sessionStorage: storage, CSS: {escape: s => s}, matchMedia: () => ({matches: false})});
window.location = {pathname: '/chat', hash: '', search: '', protocol: 'http:', host: '127.0.0.1:18100'};
globalThis.location = window.location;
window.history = {state: null, pushState() {}, replaceState() {}};
globalThis.history = window.history;
globalThis.getComputedStyle = el => ({display: el.style?.display === 'none' ? 'none' : 'block', backgroundColor: 'transparent', color: 'gray'});
const frames = [];
globalThis.requestAnimationFrame = callback => frames.push(callback);
globalThis.DOMRect = class { constructor(x = 0, y = 0, w = 0, h = 0) { Object.assign(this, {left: x, top: y, width: w, height: h, x, y, right: x + w, bottom: y + h}); } };
const proto = Object.getPrototypeOf(document.createElement('div'));
proto.getBoundingClientRect = function () {
  if (this.hasAttribute('data-canvas-root')) return new DOMRect(0, 0, 1000, 600);
  if (this.getAttribute('role') === 'tab') return rects.get('tab:' + this.getAttribute('data-tab-id')) ?? new DOMRect(0, 0, 0, 0);
  const key = this.getAttribute('data-center-tab-id');
  return rects.get(key) ?? new DOMRect(0, 0, 0, 0);
};
proto.animate = function (frames, options) {
  const finished = Promise.resolve();
  animations.push({el: this, frames, options});
  return {finished, cancel() {}};
};
const {captureCanvasTransition, playCanvasTransition, prepareCanvasFold, canvasPanesMoving} = await import(pathToFileURL(join(dir, 'probe.mjs')));
const canvas = document.querySelector('[data-canvas-root]');

test('a close plays once its tab is gone: the ghost is a still copy and the other pane grows', () => {
  rects.set('a', new DOMRect(0, 0, 500, 600));
  rects.set('b', new DOMRect(500, 0, 500, 600));
  captureCanvasTransition({b: 'close'});
  assert.equal(canvasPanesMoving(), true, 'web panes freeze from the capture on');
  playCanvasTransition();
  assert.equal(animations.length, 0, 'the tab is still here, so the change has not committed');
  canvas.querySelector('[data-center-tab-id="b"]').remove();
  rects.set('a', new DOMRect(0, 0, 1000, 600));
  playCanvasTransition();
  const move = animations.find(a => a.el.getAttribute('data-center-tab-id') === 'a');
  assert.deepEqual([move.frames[0].width, move.frames[1].width], ['500px', '1000px']);
  const ghost = canvas.querySelector('[data-canvas-ghost]');
  assert.ok(ghost, 'the closed pane leaves as a ghost');
  assert.equal(ghost.querySelector('[id]'), null, 'nothing may find the ghost by id');
  assert.equal(ghost.hasAttribute('data-center-tab-id'), false);
  assert.equal(canvas.hasAttribute('data-canvas-animating'), true);
  const leave = animations.find(a => a.el === ghost);
  assert.deepEqual(leave.frames.at(-1), {transform: 'translateX(500px)', clipPath: 'inset(0 500px 0 0)'},
    'the closed pane narrows out at the far edge instead of fading');
  assert.deepEqual([leave.options.duration, leave.options.easing], [move.options.duration, move.options.easing],
    'the closing pane and the growing pane share one clock');
});

test('a capture the layout never follows lapses instead of playing later', () => {
  animations.length = 0;
  const realNow = performance.now.bind(performance);
  captureCanvasTransition({a: 'close'});
  performance.now = () => realNow() + 5000;
  rects.set('a', new DOMRect(0, 0, 400, 600));
  canvas.querySelector('[data-center-tab-id="a"]').setAttribute('data-center-tab-id', 'c');
  playCanvasTransition();
  performance.now = realNow;
  assert.equal(animations.length, 0);
});

test('yellow folds the pane into the bottom edge of its tab, then the tab lights once', () => {
  animations.length = 0;
  canvas.innerHTML = '<div data-center-tab-id="a">A</div><div data-center-tab-id="b"><p>B</p></div>';
  const strip = document.createElement('div');
  strip.setAttribute('role', 'tablist');
  strip.innerHTML = '<div class="split"><div role="tab" data-tab-id="a"></div></div>';
  document.body.prepend(strip);
  rects.set('tab:b', new DOMRect(600, -36, 160, 28));
  rects.set('a', new DOMRect(0, 0, 500, 600));
  rects.set('b', new DOMRect(500, 0, 500, 600));
  captureCanvasTransition({b: 'minimize'});
  canvas.querySelector('[data-center-tab-id="b"]').remove();
  // Leaving the split gives the pane a tab button of its own.
  strip.innerHTML = '<div class="tab"><div role="tab" data-tab-id="a"></div></div><div class="tab"><div role="tab" data-tab-id="b"></div></div>';
  const tab = strip.lastElementChild;
  tab.getBoundingClientRect = () => rects.get('tab:b');
  rects.set('a', new DOMRect(0, 0, 1000, 600));
  playCanvasTransition();
  const layer = canvas.querySelector('[data-canvas-ghost]');
  assert.ok(layer, 'the folding pane leaves as a ghost layer');
  assert.ok(layer.children.length >= 6, 'cut into bands');
  assert.match(layer.style.clipPath, /-8px/, 'nothing is drawn above the tab bottom edge');
  assert.equal(layer.querySelector('[data-center-tab-id]'), null);
  const grow = animations.find(a => a.el.getAttribute('data-center-tab-id') === 'a');
  assert.ok(grow.options.delay > 0, 'the neighbour fills after the fold leads');
  const reveal = animations.find(a => a.el === tab);
  assert.equal(reveal?.frames[0].opacity, 0, 'the new tab button slides out before the pane folds into it');
  assert.ok(Object.keys(reveal.frames[0]).every(key => ['transform', 'transformOrigin', 'opacity'].includes(key)), 'compositor-only, so a busy main thread cannot stall it');
  let clock = 0;
  while (frames.length) frames.shift()(clock += 16);
  assert.ok(animations.some(a => a.el === tab && a.frames[0].backgroundColor), 'the whole tab button lights as it takes the pane in');
  assert.equal(layer.isConnected, false, 'the layer is gone once the fold ends');
  strip.remove();
});

test('with the desktop shell the fold draws one still of the pane, row by row, below the neck only', async () => {
  animations.length = 0;
  frames.length = 0;
  canvas.innerHTML = '<div data-center-tab-id="a">A</div><div data-center-tab-id="b"><p>B</p></div>';
  const strip = document.createElement('div');
  strip.setAttribute('role', 'tablist');
  strip.innerHTML = '<div class="tab"><div role="tab" data-tab-id="b"></div></div>';
  document.body.prepend(strip);
  const tab = strip.firstElementChild;
  tab.getBoundingClientRect = () => new DOMRect(600, -36, 160, 28);
  rects.set('a', new DOMRect(0, 0, 500, 600));
  rects.set('b', new DOMRect(500, 0, 500, 600));
  const asked = [];
  window.openprogramDesktop = {captureRegion: async (rect, options) => { asked.push([rect, options]); return 'data:image/jpeg;base64,AA'; }};
  globalThis.Image = class { decode() { return Promise.resolve(); } get naturalWidth() { return 1000; } get naturalHeight() { return 1200; } };
  const draws = [];
  const canvasProto = Object.getPrototypeOf(document.createElement('canvas'));
  canvasProto.getContext = () => ({setTransform() {}, clearRect() {}, drawImage: (...args) => draws.push(args), globalAlpha: 1});
  const ready = prepareCanvasFold('b');
  assert.ok(ready, 'the shell can take a still');
  await ready;
  assert.deepEqual(asked[0][1], {full: true}, 'a full-resolution still, not a thumbnail');
  captureCanvasTransition({b: 'minimize'});
  canvas.querySelector('[data-center-tab-id="b"]').remove();
  rects.set('a', new DOMRect(0, 0, 1000, 600));
  playCanvasTransition();
  const layer = canvas.querySelector('canvas[data-canvas-ghost]');
  assert.ok(layer, 'one canvas, no copies of the pane');
  assert.equal(canvas.querySelectorAll('[data-canvas-ghost] [data-canvas-ghost], [data-canvas-ghost] p').length, 0);
  assert.equal(draws.length, 300, 'the first frame already shows the whole pane in 2px rows');
  assert.ok(draws.every(d => d[6] >= 0), 'never drawn above the tab bottom edge');
  let clock = 0;
  while (frames.length) frames.shift()(clock += 16);
  assert.equal(layer.isConnected, false);
  delete window.openprogramDesktop;
  strip.remove();
});
