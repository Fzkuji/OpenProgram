import test from 'node:test';
import assert from 'node:assert/strict';
import { deflateRawSync } from 'node:zlib';
import { registerHooks } from 'node:module';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const moduleUrl = `/api/documents/office-module/${'b'.repeat(64)}.js`;
registerHooks({ resolve(specifier, context, next) {
  if (specifier === moduleUrl) return {url: 'data:text/javascript,' + encodeURIComponent(`
    export const mountOfficeEditor = (...args) => globalThis.officeFixture.mount(...args);
  `), shortCircuit: true};
  // Sibling modules are imported without their extension.
  if (specifier.startsWith('./') && !/\.[a-z]+$/i.test(specifier) && context.parentURL?.includes('/lib/')) {
    const url = new URL(specifier + '.ts', context.parentURL);
    if (existsSync(fileURLToPath(url))) return {url: url.href, shortCircuit: true};
  }
  return next(specifier, context);
} });
const { countOfficeUnits, incompleteRead } = await import('../../../lib/documents/office-fidelity.ts');
const { createBoundOfficeEditor } = await import('../../../lib/documents/office-editor.ts');

/** A ZIP package: deflated parts, or stored ones with `stored`. */
export function zip(parts, { stored = false } = {}) {
  const chunks = [], directory = [];
  let offset = 0;
  for (const [name, text] of Object.entries(parts)) {
    const raw = Buffer.from(text), data = stored ? raw : deflateRawSync(raw), file = Buffer.from(name);
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(stored ? 0 : 8, 8);
    local.writeUInt32LE(data.length, 18); local.writeUInt32LE(raw.length, 22); local.writeUInt16LE(file.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(stored ? 0 : 8, 10);
    central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24); central.writeUInt16LE(file.length, 28);
    central.writeUInt32LE(offset, 42);
    chunks.push(local, file, data); directory.push(central, file);
    offset += local.length + file.length + data.length;
  }
  const size = directory.reduce((n, b) => n + b.length, 0), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(directory.length / 2, 8); end.writeUInt16LE(directory.length / 2, 10);
  end.writeUInt32LE(size, 12); end.writeUInt32LE(offset, 16);
  return new Blob([...chunks, ...directory, end]);
}
const deck = (n, extra = {}) => zip({ '[Content_Types].xml': '<Types/>',
  'ppt/presentation.xml': `<p:presentation xmlns:p="p"><p:sldIdLst>${'<p:sldId id="1" r:id="r"/>'.repeat(n)}</p:sldIdLst></p:presentation>`, ...extra });

test('slides and sheets are counted from the parts that list them, deflated or stored', async () => {
  assert.equal(await countOfficeUnits(deck(10), 'talk.pptx'), 10);
  assert.equal(await countOfficeUnits(deck(0), 'talk.pptx'), 0);
  const book = zip({ 'xl/workbook.xml': '<workbook><sheets><sheet name="a"/><sheet name="b"/></sheets></workbook>' }, { stored: true });
  assert.equal(await countOfficeUnits(book, 'budget.xlsx'), 2);
  assert.equal(await countOfficeUnits(deck(3), 'notes.docx'), null, 'a format without listed units is not judged');
  assert.equal(await countOfficeUnits(new Blob(['not a zip']), 'talk.pptx'), null);
});

test('an export that kept fewer slides than the file holds is an incomplete read', async () => {
  assert.deepEqual(await incompleteRead(deck(10), deck(0), 'talk.pptx'), { expected: 10, kept: 0 });
  assert.deepEqual(await incompleteRead(deck(10), deck(1), 'talk.pptx'), { expected: 10, kept: 1 });
  assert.equal(await incompleteRead(deck(10), deck(10), 'talk.pptx'), null);
  assert.equal(await incompleteRead(deck(0), deck(0), 'talk.pptx'), null, 'an empty deck read as empty is whole');
});

function engine(exported) {
  const calls = { readonly: [], staged: 0, attached: 0, dirty: 0, destroyed: 0 };
  let options;
  const instance = {
    getState: () => ({ status: 'ready', readonly: false, dirty: false }),
    setReadonly: value => calls.readonly.push(value),
    flushPendingSaves: async () => {},
    destroy: async () => { calls.destroyed++; },
    // The engine hands its export to the save callback, as the host does.
    save: async () => { await options.onSave(new File([exported], 'x.pptx')); options.onDirtyChange(true, instance); return new File([exported], 'x.pptx'); },
  };
  globalThis.officeFixture = { mount(_container, value) { options = value; return { activate: async () => instance, destroy: instance.destroy }; } };
  const controller = { getState: () => ({ editorRevision: 0 }),
    attachRichEditor: () => { calls.attached++; return () => {}; },
    stageRichExport: async () => { calls.staged++; },
    markRichEditorDirty: () => { calls.dirty++; } };
  return { calls, controller };
}
const open = (source, controller) => createBoundOfficeEditor({ container: {}, controller, bytes: source, fileName: 'talk.pptx',
  moduleUrl, hostUrl: 'http://host-test.office.localhost:18100/office-host.html', initialMode: 'edit' });

test('a file the editor reads with slides missing is not opened, and nothing is written', async () => {
  const { calls, controller } = engine(await deck(0).arrayBuffer());
  await assert.rejects(open(deck(10), controller), error => error.name === 'IncompleteReadError'
    && error.read.expected === 10 && error.read.kept === 0 && /not opened/.test(error.message));
  assert.equal(calls.destroyed, 1, 'the engine is closed');
  assert.equal(calls.attached, 0);
  assert.equal(calls.staged, 0, 'the check export is compared, never kept as a draft');
  assert.equal(calls.dirty, 0);
  delete globalThis.officeFixture;
});

test('an editor that read every slide is bound and editable', async () => {
  const { calls, controller } = engine(await deck(10).arrayBuffer());
  const editor = await open(deck(10), controller);
  assert.ok(editor);
  assert.deepEqual(calls.readonly, []);
  assert.equal(calls.attached, 1);
  assert.equal(calls.staged, 0);
  delete globalThis.officeFixture;
});
