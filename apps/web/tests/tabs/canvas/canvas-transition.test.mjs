import assert from 'node:assert/strict';
import test from "node:test";
import {dirname, join} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'esbuild';

import { scratchDir } from "../../scratch-dir.mjs";

const root = dirname(fileURLToPath(new URL('../../../package.json', import.meta.url)));
const require = createRequire(join(root, 'package.json'));
const {parseHTML} = require('linkedom');
const dir = scratchDir(root, "canvas-transition");
await build({absWorkingDir: root, stdin: {contents: `
export {captureCanvasTransition, playCanvasTransition, foldClip} from './lib/tabs/canvas/canvas-transition';
export {canvasPanesMoving} from './lib/tabs/canvas/canvas-motion';
`, resolveDir: root}, bundle: true, format: 'esm', platform: 'node', packages: 'external',
  tsconfig: join(root, 'tsconfig.json'), outfile: join(dir, 'probe.mjs'), loader: {'.css': 'empty'}});

const {window} = parseHTML('<html><body><div data-canvas-root><div id="chatArea-shell" data-center-tab-id="a"><div id="chatArea" class="chat-area">A</div></div><div data-center-tab-id="b">B</div></div></body></html>');
const document = window.document;
const rects = new Map();
const animations = [];
const store = new Map();
const storage = {getItem: k => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)), removeItem: k => store.delete(k)};
Object.assign(globalThis, {window, document, localStorage: storage, sessionStorage: storage, CustomEvent: window.CustomEvent, CSS: {escape: s => s}, matchMedia: () => ({matches: false})});
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
  animations.push({el: this, frames, options, finishedLater: new Promise(resolve => setTimeout(resolve, 5))});
  return {finished, cancel() {}};
};
const {captureCanvasTransition, playCanvasTransition, canvasPanesMoving, foldClip} = await import(pathToFileURL(join(dir, 'probe.mjs')));
const canvas = document.querySelector('[data-canvas-root]');

test('a pane a rearrangement hides fades under the pane that grows into its room', () => {
  rects.set('a', new DOMRect(0, 0, 500, 600));
  rects.set('b', new DOMRect(500, 0, 500, 600));
  captureCanvasTransition();
  assert.equal(canvasPanesMoving(), true, 'web panes freeze from the capture on');
  canvas.querySelector('[data-center-tab-id="b"]').remove();
  rects.set('a', new DOMRect(0, 0, 1000, 600));
  playCanvasTransition();
  const move = animations.find(a => a.el.getAttribute('data-center-tab-id') === 'a');
  assert.deepEqual([move.frames[0].width, move.frames[1].width], ['500px', '1000px']);
  const ghost = canvas.querySelector('[data-canvas-ghost]');
  assert.ok(ghost, 'the hidden pane leaves as a ghost');
  assert.equal(ghost.querySelector('[id]'), null, 'nothing may find the ghost by id');
  assert.equal(ghost.hasAttribute('data-center-tab-id'), false);
  assert.equal(canvas.firstElementChild, ghost, 'it stays under the panes that take its place');
  assert.equal(ghost.style.zIndex, '0');
  assert.equal(canvas.hasAttribute('data-canvas-animating'), true);
  const leave = animations.find(a => a.el === ghost);
  assert.deepEqual(leave.frames.map(f => f.opacity), [1, 0]);
  assert.equal(leave.options.duration, move.options.duration, 'the fade and the growing pane share one clock');
});

test('a capture the layout never follows lapses instead of playing later', () => {
  animations.length = 0;
  const realNow = performance.now.bind(performance);
  captureCanvasTransition();
  performance.now = () => realNow() + 5000;
  rects.set('a', new DOMRect(0, 0, 400, 600));
  canvas.querySelector('[data-center-tab-id="a"]').setAttribute('data-center-tab-id', 'c');
  playCanvasTransition();
  performance.now = realNow;
  assert.equal(animations.length, 0);
});

test('a closing pane folds toward its own side as the pane beside it grows into its room', () => {
  const left = new DOMRect(0, 0, 500, 600), right = new DOMRect(500, 0, 500, 600), whole = new DOMRect(0, 0, 1000, 600);
  assert.equal(foldClip(right, [{from: left, to: whole}]), 'inset(0 0 0 100%)', 'the right pane folds to the right edge');
  assert.equal(foldClip(left, [{from: right, to: whole}]), 'inset(0 100% 0 0)', 'the left pane folds to the left edge');
  const top = new DOMRect(0, 0, 1000, 300), bottom = new DOMRect(0, 300, 1000, 300);
  assert.equal(foldClip(bottom, [{from: top, to: whole}]), 'inset(100% 0 0 0)');
  assert.equal(foldClip(top, [{from: bottom, to: whole}]), 'inset(0 0 100% 0)');
  assert.equal(foldClip(right, [{from: left, to: left}]), null, 'no pane takes its room: nothing to fold toward');
});

test('closing copies only the closing pane, and a document pane that stays mounted folds itself', async () => {
  for (const node of [...canvas.children]) node.remove();
  canvas.innerHTML = '<div data-center-tab-id="a">A</div><div><div data-file-pane-id="f">F</div></div>';
  animations.length = 0;
  rects.set('a', new DOMRect(0, 0, 500, 600));
  const file = canvas.querySelector('[data-file-pane-id]');
  file.getBoundingClientRect = () => new DOMRect(500, 0, 500, 600);
  captureCanvasTransition({}, {closing: ['f'], held: ['f']});
  // The tab is gone; its pane stays mounted where it stood for the fold.
  file.removeAttribute('data-file-pane-id');
  file.setAttribute('data-file-pane-leaving', 'f');
  rects.set('a', new DOMRect(0, 0, 1000, 600));
  playCanvasTransition();
  assert.equal(canvas.querySelector('[data-canvas-ghost]'), null, 'no copy of the chat or of the document is made');
  const fold = animations.find(a => a.el === file);
  assert.deepEqual(fold.frames.map(f => f.clipPath), ['inset(0 0 0 0)', 'inset(0 0 0 100%)']);
  const grow = animations.find(a => a.el.getAttribute('data-center-tab-id') === 'a');
  assert.deepEqual([grow.frames[0].width, grow.frames[1].width], ['500px', '1000px']);
  assert.equal(fold.options.duration, grow.options.duration, 'the fold and the growing pane share one clock');
  assert.equal(fold.options.easing, grow.options.easing);
  let folded = null;
  window.addEventListener('op-file-pane-folded', event => { folded = event.detail.tabId; });
  await fold.finishedLater;
  assert.equal(folded, 'f', 'the pane is told to go out of sight when the fold ends');
});
