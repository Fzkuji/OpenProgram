import assert from 'node:assert/strict';
import test from 'node:test';
import { registerHooks } from 'node:module';
import { parseHTML } from 'linkedom';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

const mocks = {
  '@/lib/format-utils/toast': 'export const showToast = (m) => globalThis.voiceProbe.toasts.push(m);',
  '@/lib/i18n': 'export const useTranslation = () => ({ text: (en) => en, t: (en) => en });',
  '@/lib/voice/segmenter': 'export const joinPhrase = (_before, phrase) => phrase;',
  '@/lib/voice/recorder': 'export class VoiceRecorder { constructor() { globalThis.voiceProbe.recorders.push(this); this.released = false; this.stopped = false; } start() { return globalThis.voiceProbe.mic.promise; } stop() { this.stopped = true; this.released = true; } cancel() { this.released = true; } }',
};
registerHooks({
  resolve(specifier, context, next) {
    if (mocks[specifier]) return { url: 'data:text/javascript,' + encodeURIComponent(mocks[specifier]), shortCircuit: true };
    return next(specifier, context);
  },
});
const { window } = parseHTML('<!doctype html><html><body></body></html>');
Object.assign(globalThis, { window, document: window.document, IS_REACT_ACT_ENVIRONMENT: true });
globalThis.requestAnimationFrame ??= (fn) => setTimeout(fn, 0);
const { useVoiceInput } = await import('../../../components/chat/composer/voice/use-voice-input.ts');

function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  promise.catch(() => {});
  return { promise, resolve, reject };
}

async function fixture(fn) {
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const probe = { toasts: [], recorders: [], hook: null, mic: deferred() };
  globalThis.voiceProbe = probe;
  const status = deferred();
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ json: () => status.promise });
  const textareaRef = { current: null };
  const wrapperRef = { current: null };
  function Probe() {
    const [input, setInput] = React.useState('');
    probe.hook = useVoiceInput({ textareaRef, input, setInput, wrapperRef, owner: 'chat-a' });
    return null;
  }
  await act(async () => root.render(React.createElement(Probe)));
  let mounted = true;
  const unmount = async () => { mounted = false; await act(async () => root.unmount()); };
  try {
    await fn({ probe, status, wrapperRef, unmount });
  } finally {
    globalThis.fetch = originalFetch;
    if (mounted) await act(async () => root.unmount());
    host.remove();
  }
}

test('Esc during the status check means the microphone never opens', () => fixture(async ({ probe, status }) => {
  let started;
  await act(async () => { started = probe.hook.start(); });
  assert.equal(probe.hook.state, 'starting');
  await act(async () => probe.hook.cancel());
  await act(async () => { status.resolve({ resolved_engine: 'local' }); await started; });
  assert.equal(probe.recorders.length, 0);
  assert.equal(probe.hook.state, 'idle');
}));

test('Esc while the microphone opens releases it instead of recording', () => fixture(async ({ probe, status }) => {
  let started;
  status.resolve({ resolved_engine: 'local' });
  await act(async () => { started = probe.hook.start(); });
  assert.equal(probe.recorders.length, 1);
  await act(async () => probe.hook.cancel());
  await act(async () => { probe.mic.resolve(); await started; });
  assert.equal(probe.recorders[0].released, true);
  assert.equal(probe.hook.state, 'idle');
}));

test('unmounting while the microphone opens leaves no open stream', () => fixture(async ({ probe, status, unmount }) => {
  let started;
  status.resolve({ resolved_engine: 'local' });
  await act(async () => { started = probe.hook.start(); });
  await unmount();
  probe.mic.resolve();
  await started;
  assert.equal(probe.recorders[0].released, true);
}));

test('a recorder that fails half-way is released', () => fixture(async ({ probe, status }) => {
  status.resolve({ resolved_engine: 'local' });
  await act(async () => { const run = probe.hook.start(); probe.mic.reject(new Error('AudioContext failed')); await run; });
  assert.equal(probe.recorders[0].released, true);
  assert.equal(probe.hook.state, 'idle');
  assert.equal(probe.toasts.length, 1);
}));

test('releasing hold-to-talk before the microphone opens stops right after it does', () => fixture(async ({ probe, status, wrapperRef }) => {
  const box = document.createElement('div');
  const textarea = document.createElement('textarea');
  box.append(textarea);
  document.body.append(box);
  wrapperRef.current = box;
  // linkedom has no focus model: point activeElement at the textarea.
  Object.defineProperty(document, 'activeElement', { configurable: true, get: () => textarea });
  const fire = (type, init) => {
    const event = new window.Event(type, { bubbles: true, cancelable: true });
    for (const [key, value] of Object.entries(init)) Object.defineProperty(event, key, { value });
    window.dispatchEvent(event);
  };
  try {
    status.resolve({ resolved_engine: 'local' });
    await act(async () => { fire('keydown', { code: 'Space', key: ' ', altKey: true, metaKey: false, ctrlKey: false, repeat: false }); });
    assert.equal(probe.hook.state, 'starting');
    await act(async () => { fire('keyup', { code: 'Space', key: ' ', altKey: true }); });
    await act(async () => { probe.mic.resolve(); await new Promise((r) => setTimeout(r, 0)); });
    assert.equal(probe.recorders[0].stopped, true);
    assert.notEqual(probe.hook.state, 'recording');
  } finally {
    delete document.activeElement;
    box.remove();
  }
}));
