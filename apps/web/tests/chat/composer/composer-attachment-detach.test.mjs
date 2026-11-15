/**
 * The real useComposerAttachments hook, rendered with React: a submitted
 * file leaves the composer when the socket accepts the frame (the backend
 * ACK can trail it by seconds), and a rejection puts it back in place.
 * IndexedDB, the session store and the desktop bridge are test doubles.
 */
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../../", import.meta.url);
const host = globalThis.__attachDetach = { saved: [], stored: { images: [], docs: [] }, activeChatKey: "A" };
const mocks = {
  "@/lib/session-store": `const host = globalThis.__attachDetach;
    export const useSessionStore = (select) => select({ activeChatKey: host.activeChatKey });`,
  "@/lib/desktop/desktop-bridge": "export const desktopBridge = () => null;",
  "./attach-idb": `const host = globalThis.__attachDetach;
    export const markAttachmentOwnerClosed = () => {};
    export const attachmentOwnerIsClosed = () => false;
    export const onAttachmentOwnerClosed = () => () => {};
    export const loadAttachments = async () => host.stored;
    export const saveAttachments = async (key, data) => { host.saved.push([key, data]); };
    export const deleteAttachments = async () => {};`,
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (Object.hasOwn(mocks, specifier)) {
      return { url: "data:text/javascript," + encodeURIComponent(mocks[specifier]), shortCircuit: true };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = base + suffix;
        if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
      }
    }
    return nextResolve(specifier, context);
  },
});

const parsed = parseHTML('<!doctype html><html><body><div id="root"></div></body></html>');
globalThis.window = parsed.window;
globalThis.document = parsed.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const revoked = [];
globalThis.URL.revokeObjectURL = (url) => { revoked.push(url); };

const { useComposerAttachments } = await import(
  "../../../components/chat/composer/attach/use-composer-attachments.ts"
);

function doc(id, order) {
  return { id, filename: `${id}.pdf`, ext: "pdf", content: null, dataB64: null,
    mediaType: "application/pdf", sizeBytes: 10, order };
}
function image(id, order) {
  return { id, previewUrl: `blob:${id}`, sizeBytes: 10, order,
    attachment: { type: "image", data: "aaa", media_type: "image/png", filename: `${id}.png` } };
}

async function mount(stored) {
  host.stored = stored;
  host.saved = [];
  revoked.length = 0;
  let hook;
  function Probe() { hook = useComposerAttachments(); return null; }
  const root = createRoot(document.getElementById("root"));
  await act(async () => { root.render(createElement(Probe)); });
  await act(async () => { await Promise.resolve(); });
  return { get: () => hook, unmount: () => act(async () => root.unmount()) };
}

test("a submitted file leaves the composer on send and its preview is freed on ACK", async () => {
  const view = await mount({ images: [image("shot", 0)], docs: [doc("statement", 1)] });
  assert.deepEqual(view.get().pendingDocs.map((d) => d.id), ["statement"]);
  let submitted;
  await act(async () => {
    submitted = view.get().detachForSubmit("A", { imageIds: ["shot"], docIds: ["statement"] });
  });
  assert.deepEqual(view.get().pendingDocs, []);
  assert.deepEqual(view.get().pendingImages, []);
  assert.deepEqual(revoked, [], "the preview stays alive until the backend decides");
  await act(async () => { submitted.release(); submitted.restore(); });
  assert.deepEqual(revoked, ["blob:shot"]);
  assert.deepEqual(view.get().pendingImages, [], "a settled send cannot come back");
  await view.unmount();
});

test("a rejected send puts its files back beside files added since", async () => {
  const view = await mount({ images: [], docs: [doc("first", 0), doc("kept", 1)] });
  let submitted;
  await act(async () => {
    submitted = view.get().detachForSubmit("A", { imageIds: [], docIds: ["first"] });
  });
  assert.deepEqual(view.get().pendingDocs.map((d) => d.id), ["kept"]);
  await act(async () => { submitted.restore(); });
  assert.deepEqual(view.get().pendingDocs.map((d) => d.id).sort(), ["first", "kept"]);
  assert.equal(view.get().pendingDocs.find((d) => d.id === "first").order, 0);
  assert.deepEqual(host.saved.at(-1)[1].docs.map((d) => d.id).sort(), ["first", "kept"]);
  assert.deepEqual(revoked, []);
  await view.unmount();
});
