/**
 * Queued rows with files survive a reload: the bytes go to the composer's
 * IndexedDB store (an in-memory double here), localStorage keeps only the
 * row, and a restored row sends with its files or waits for a retry.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(`../../../${specifier.slice(2)}`, import.meta.url).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    if (specifier.startsWith(".") && !/\.[a-z]+$/.test(specifier)) {
      const base = new URL(specifier, context.parentURL).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const idb = new Map();
const backend = {
  async save(id, data) { idb.set(id, structuredClone(data)); },
  async load(id) { return idb.has(id) ? structuredClone(idb.get(id)) : null; },
  async remove(id) { idb.delete(id); },
};
const { setQueuedAttachmentBackend, syncQueuedAttachments } = await import("../../../lib/chat/send/queued-attachment-store.ts");
setQueuedAttachmentBackend(backend);
const { useSendQueue, registerChatSender, restorePendingAttachments } = await import("../../../lib/chat/send/send-queue.ts");
const { useSessionStore } = await import("../../../lib/session-store/index.ts");

const image = {
  id: "img", sizeBytes: 3, previewUrl: "blob:x",
  attachment: { type: "image", data: "AAA=", media_type: "image/png", filename: "a.png" },
};
const doc = { id: "doc", filename: "notes.txt", ext: "txt", dataB64: "bm90ZXM=", sizeBytes: 5 };
const settings = { thinking: "medium", webSearchEnabled: false, background: false };
const flush = () => syncQueuedAttachments({}, {});

test("enqueue stores the files, removal drops them, a restored row keeps them", async () => {
  useSessionStore.getState().setRunningTaskFor("S", { session_id: "S", msg_id: "busy" }, "never");
  const id = useSendQueue.getState().enqueue("S", { ...settings, text: "see files", images: [image], docs: [doc] });
  await flush();
  assert.equal(idb.get(id).images[0].attachment.data, "AAA=");
  assert.equal(idb.get(id).docs[0].dataB64, "bm90ZXM=");

  // A failed send pops the row and puts it back: its files must remain.
  const row = useSendQueue.getState().queues.S[0];
  useSendQueue.getState().remove("S", id);
  useSendQueue.setState(s => ({ queues: { ...s.queues, S: [row] } }));
  await flush();
  assert.ok(idb.has(id));

  useSendQueue.getState().removeDraft("S", id);
  await flush();
  assert.equal(idb.has(id), false);
  useSessionStore.getState().setRunningTaskFor("S", null, "never");
});

test("a restored row waits for its files and then sends them", async () => {
  const sent = [];
  registerChatSender(args => { sent.push(args); return true; });
  idb.set("q-restored", { images: [{ ...image, previewUrl: null }], docs: [doc] });
  useSendQueue.setState(s => ({ queues: { ...s.queues, R: [
    { id: "q-restored", text: "with files", queuedAt: 1, ...settings, attachmentsPending: true },
  ] } }));
  useSendQueue.getState().drain("R");
  assert.equal(sent.length, 0, "never sends without its files");
  assert.equal(useSendQueue.getState().beginEdit("R", "q-restored"), false);

  await restorePendingAttachments("R");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].hasAttachments, true);
  assert.equal(sent[0].attachments.length, 2, "image and document bytes travel inline");
  assert.equal(sent[0].attachments[0].data, "AAA=");
  await flush();
  assert.equal(idb.has("q-restored"), false);
});

test("a restored row whose files are gone keeps its text and waits for a retry", async () => {
  const sent = [];
  registerChatSender(args => { sent.push(args); return true; });
  useSendQueue.setState(s => ({ queues: { ...s.queues, G: [
    { id: "q-gone", text: "lost files", queuedAt: 1, ...settings, attachmentsPending: true },
  ] } }));
  await restorePendingAttachments("G");
  assert.equal(sent.length, 0);
  const [row] = useSendQueue.getState().queues.G;
  assert.equal(row.deliveryError, true);
  assert.equal(row.attachmentsPending, false);
});
