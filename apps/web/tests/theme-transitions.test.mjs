import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const chrome = require('../../desktop/theme-chrome.js');
const compiled = buildSync({ entryPoints: [fileURLToPath(new URL('../lib/prefs/theme-bootstrap.ts', import.meta.url))], bundle: true, platform: 'node', format: 'cjs', write: false });
const module = { exports: {} };
vm.runInNewContext(compiled.outputFiles[0].text, { module, exports: module.exports });

function bootstrap(mode = 'dark') {
  const values = new Map(Object.entries({ agentic_theme_schema: '3', agentic_theme_style: 'neutral', agentic_theme_mode: mode, agentic_custom_css_enabled: '0' }));
  const attrs = new Map();
  const traces = [];
  const handlers = {};
  const mq = { matches: false, addEventListener: (_, cb) => { handlers.system = cb; } };
  const root = { setAttribute: (k,v) => attrs.set(k,v), getAttribute: k => attrs.get(k) ?? null, removeAttribute: k => attrs.delete(k), style: { setProperty() {}, removeProperty() {} } };
  let observer;
  const window = { localStorage: { getItem: k => values.get(k) ?? null, setItem: (k,v) => values.set(k,v) }, matchMedia: () => mq, addEventListener: (k,cb) => { handlers[k] = cb; }, openprogramDesktop: { theme: { setChrome() {}, trace: p => traces.push(p) } } };
  vm.runInNewContext(module.exports.THEME_BOOTSTRAP_SCRIPT, { window, document: { documentElement: root, getElementById: () => null }, MutationObserver: class { constructor(cb) { observer = cb; } observe() {} } });
  return { values, traces, attrs, handlers, mq, root, get observer() { return observer; } };
}

test('real bootstrap reports initialization and cross-window changes without changing preference', () => {
  const b = bootstrap();
  assert.equal(b.traces[0]?.source, 'bootstrap');
  assert.equal(b.traces[0].theme, 'dark');
  b.values.set('agentic_theme_mode', 'light');
  b.handlers.storage({ key: 'agentic_theme_mode' });
  assert.equal(b.traces.at(-1).source, 'storage');
  assert.equal(b.traces.at(-1).previousTheme, 'dark');
  assert.equal(b.traces.at(-1).theme, 'light');
  assert.equal(b.values.get('agentic_theme_mode'), 'light');
});

test('auto follows system, fixed mode ignores system, unexpected DOM changes are observed', () => {
  const b = bootstrap('auto');
  b.mq.matches = true;
  b.handlers.system();
  assert.equal(b.traces.at(-1).source, 'system');
  assert.equal(b.traces.at(-1).theme, 'dark');
  b.root.setAttribute('data-theme', 'light');
  b.observer([{ attributeName: 'data-theme', oldValue: 'dark' }]);
  assert.equal(b.traces.at(-1).source, 'dom-change');
  assert.equal(b.traces.at(-1).previousTheme, 'dark');
  const fixed = bootstrap('dark');
  fixed.handlers.system();
  assert.equal(fixed.attrs.get('data-theme'), 'dark');
});

test('desktop diagnostic log is bounded and excludes arbitrary data', () => {
  const dir = mkdtempSync(join(tmpdir(), 'theme-trace-'));
  try {
    for (let i = 0; i < 70; i++) chrome.recordThemeEvent(dir, { source: 'settings-mode', theme: i % 2 ? 'light' : 'dark', previousTheme: 'dark', mode: 'light', storedMode: 'light', systemDark: false, text: 'private message', url: 'https://private.test' });
    const records = JSON.parse(readFileSync(join(dir, 'theme-events.json'), 'utf8'));
    assert.equal(records.length, 64);
    assert.equal(records.at(-1).theme, 'light');
    assert.equal(records.at(-1).source, 'settings-mode');
    assert.doesNotMatch(JSON.stringify(records), /private/);
    chrome.recordThemeEvent(dir, { source: 'private text', theme: 'private text' });
    const last = JSON.parse(readFileSync(join(dir, 'theme-events.json'), 'utf8')).at(-1);
    assert.equal(last.source, 'unknown');
    assert.equal(last.theme, null);
    assert.doesNotThrow(() => chrome.recordThemeEvent(join(dir, 'missing', 'nested'), {}));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
