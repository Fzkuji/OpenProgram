import { test } from "node:test";
import assert from "node:assert/strict";
import { bindPdfEditor } from "../../lib/documents/pdf-editor.ts";

function fixture() {
  let revision = 'original';
  const staged = [], listeners = new Map();
  const storage = { get serializable() { return {hash: revision}; }, resetModified() {}, onSetModified: null };
  const pdf = { annotationStorage: storage, async saveDocument() { return new TextEncoder().encode(revision); } };
  const controller = { getState: () => ({editorRevision: 7}), attachRichEditor: () => () => {}, markRichEditorDirty() {}, async stageRichExport(blob, generation) { staged.push({text: await blob.text(), generation}); } };
  const bus = { on(name, fn) { listeners.set(name, fn); }, off(name) { listeners.delete(name); } };
  const editor = bindPdfEditor(pdf, bus, {toggleAttribute() {}, closest() { return null; }}, controller);
  return {pdf, editor, staged, controller, change(value) { revision = value; storage.onSetModified?.(); }};
}
test('PDF export retains edits made while serialization is pending', async () => {
  const f = fixture(); let release, started;
  const pending = new Promise(resolve => { started = resolve; });
  f.pdf.saveDocument = () => { const value = f.pdf.annotationStorage.serializable.hash; started(); return new Promise(resolve => { release = () => resolve(new TextEncoder().encode(value)); }); };
  f.change('first'); const save = f.editor.save(undefined, {commitPendingInput:false}); await pending;
  f.change('second'); release(); await save;
  assert.equal(f.editor.getState().dirty, true);
  assert.deepEqual(f.staged, [{text:'first', generation:7}]);
  f.pdf.saveDocument = async () => new TextEncoder().encode('second');
  await f.editor.save(undefined, {commitPendingInput:false});
  assert.equal(f.editor.getState().dirty, false);
  assert.equal(f.staged.at(-1).text, 'second');
  f.editor.destroy();
});
test('PDF export failure retains dirty state and a closed editor cannot publish', async () => {
  const f = fixture(); f.change('note');
  f.controller.stageRichExport = async () => { throw Error('draft unavailable'); };
  await assert.rejects(f.editor.save(undefined, {commitPendingInput:false}), /draft unavailable/);
  assert.equal(f.editor.getState().dirty, true);
  f.editor.destroy();
  await assert.rejects(f.editor.save(undefined, {commitPendingInput:false}), /closed/);
  assert.deepEqual(f.staged, []);
});
