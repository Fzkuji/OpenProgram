import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createElement, useEffect } from "react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { parseHTML } from "linkedom";
import { build } from "esbuild";

const webRoot = new URL("../../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.endsWith(".module.css")) {
      return {
        url: "data:text/javascript,export default new Proxy({}, { get: (_, key) => String(key) });",
        shortCircuit: true,
      };
    }
    const resolveBase = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
    if (resolveBase) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        const url = `${resolveBase}${suffix}`;
        if (existsSync(fileURLToPath(url))) {
          return { url, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
});

const parsed = parseHTML(
  '<!doctype html><html><body><div id="root"></div></body></html>',
);
globalThis.window = parsed.window;
globalThis.document = parsed.document;
globalThis.Event = parsed.window.Event;
globalThis.CustomEvent = parsed.window.CustomEvent;
globalThis.localStorage = {
  getItem(key) { return key === "agentic_locale" ? "en" : null; },
  setItem() {},
  removeItem() {},
};
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
});
Object.defineProperty(window, "location", {
  value: { pathname: "/chat" },
  configurable: true,
});
globalThis.WebSocket = { OPEN: 1 };

const {
  addDocsForChat,
  addImagesForChat,
  mergeAttachments,
  nextAttachmentOrder,
  orderedComposerAttachments,
  removeDocForChat,
  removeImageForChat,
  attachmentsBlockSend,
} = await import(
  "../../components/chat/composer/attach/attachment-session-cache.ts"
);
const { planDroppedFiles, formatAttachmentLabel, formatAttachmentSize } =
  await import("../../components/chat/composer/attach/image-attach.ts");
const { useChatSubmit } = await import(
  "../../components/chat/composer/submit/use-chat-submit.ts"
);

function fileLike(name, type, size = 2048) {
  return { name, type, size };
}

function imageItem(id, order, extra = {}) {
  return {
    id,
    previewUrl: extra.previewUrl ?? null,
    sizeBytes: extra.sizeBytes ?? 1363149,
    attachment: {
      type: "image",
      data: extra.data ?? "aaa",
      media_type: "image/png",
      filename: extra.filename ?? `${id}.png`,
    },
    order,
    loading: extra.loading,
    error: extra.error,
  };
}

function docItem(id, order, extra = {}) {
  return {
    id,
    filename: extra.filename ?? `${id}.pdf`,
    ext: extra.ext ?? "pdf",
    content: extra.content ?? null,
    dataB64: extra.dataB64 ?? "bbb",
    mediaType: extra.mediaType ?? "application/pdf",
    sizeBytes: extra.sizeBytes ?? 862208,
    order,
    loading: extra.loading,
    error: extra.error,
  };
}

test("mixed drop order is preserved by monotonic order, not images-first arrays", () => {
  const planned = planDroppedFiles([
    fileLike("report.pdf", "application/pdf"),
    fileLike("shot.png", "image/png"),
    fileLike("notes.txt", "text/plain"),
  ], 0);
  assert.equal(planned.docs[0].file.name, "report.pdf");
  assert.equal(planned.docs[0].order, 0);
  assert.equal(planned.images[0].file.name, "shot.png");
  assert.equal(planned.images[0].order, 1);
  assert.equal(planned.docs[1].file.name, "notes.txt");
  assert.equal(planned.docs[1].order, 2);

  const byChat = new Map();
  addImagesForChat(byChat, "c1", [
    imageItem("shot", planned.images[0].order, { filename: "shot.png" }),
  ]);
  addDocsForChat(byChat, "c1", [
    docItem("report", planned.docs[0].order, { filename: "report.pdf" }),
    docItem("notes", planned.docs[1].order, {
      filename: "notes.txt", ext: "txt", sizeBytes: 18432,
    }),
  ]);
  const ordered = orderedComposerAttachments(byChat.get("c1"));
  assert.deepEqual(ordered.map((entry) => entry.item.id), [
    "report", "shot", "notes",
  ]);
});

test("removal keeps remaining insertion order", () => {
  const byChat = new Map();
  addDocsForChat(byChat, "c1", [docItem("report", 0)]);
  addImagesForChat(byChat, "c1", [imageItem("shot", 1)]);
  addDocsForChat(byChat, "c1", [docItem("sheet", 2, {
    filename: "Annual Report 2026.xlsx", ext: "xlsx",
  })]);
  removeImageForChat(byChat, "c1", "shot");
  assert.deepEqual(
    orderedComposerAttachments(byChat.get("c1")).map((entry) => entry.item.id),
    ["report", "sheet"],
  );
});

test("IDB merge keeps mixed order metadata instead of images-first", () => {
  const persisted = {
    images: [imageItem("shot", 1)],
    docs: [docItem("report", 0), docItem("notes", 2, { filename: "notes.txt", ext: "txt" })],
  };
  const inMemory = {
    images: [imageItem("shot", 1, { data: "live" })],
    docs: persisted.docs,
  };
  const merged = mergeAttachments(persisted, inMemory);
  assert.deepEqual(
    orderedComposerAttachments(merged).map((entry) => entry.item.id),
    ["report", "shot", "notes"],
  );
  assert.equal(merged.images[0].attachment.data, "live");
  assert.equal(nextAttachmentOrder(merged), 3);
});

test("legacy items without order stay images-then-docs and do not sort by id", () => {
  const ordered = orderedComposerAttachments({
    images: [imageItem("z-image"), imageItem("a-image")],
    docs: [docItem("m-doc")],
  });
  assert.deepEqual(ordered.map((entry) => entry.item.id), [
    "z-image", "a-image", "m-doc",
  ]);
});

test("loading or read error blocks send and names the reason", () => {
  assert.equal(attachmentsBlockSend([imageItem("ok", 0)], []), null);
  assert.equal(
    attachmentsBlockSend([imageItem("shot", 0, { loading: true })], []),
    "loading",
  );
  assert.equal(
    attachmentsBlockSend([], [docItem("report", 0, { error: "Read failed" })]),
    "error",
  );
});

test("format line shows File for unknown types and omits invented folder size", () => {
  assert.equal(formatAttachmentLabel(""), "File");
  assert.equal(formatAttachmentLabel("pdf"), "PDF");
  assert.equal(formatAttachmentLabel("folder"), "Folder");
  assert.equal(formatAttachmentSize(1363149), "1.3 MB");
  assert.equal(formatAttachmentSize(862208), "842 KB");
  assert.equal(formatAttachmentSize(null, { folder: true }), "");
});

test("compact strip renders mixed order, separate remove, loading and error", async () => {
  const webPath = fileURLToPath(webRoot);
  const bundled = await build({
    absWorkingDir: webPath,
    entryPoints: ["components/chat/composer/attach/attachment-strip.tsx"],
    bundle: true,
    write: false,
    format: "esm",
    platform: "node",
    jsx: "automatic",
    packages: "external",
    loader: { ".module.css": "empty" },
    alias: {
      "@/lib/i18n": fileURLToPath(new URL("../../lib/i18n/index.ts", import.meta.url)),
    },
  });
  const dir = mkdtempSync(join(webPath, ".attach-strip-"));
  const out = join(dir, "strip.mjs");
  writeFileSync(out, bundled.outputFiles[0].text);
  const { AttachmentStrip } = await import(pathToFileURL(out).href);
  const removed = [];
  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  const images = [
    imageItem("shot", 1, { filename: "Screenshot.png", previewUrl: "blob:shot" }),
  ];
  const docs = [
    docItem("report", 0, { filename: "report.pdf", loading: true }),
    docItem("broken", 2, { filename: "broken.bin", ext: "", error: "Read failed", sizeBytes: 12 }),
  ];
  await act(async () => {
    root.render(createElement(AttachmentStrip, {
      pendingImages: images,
      pendingDocs: docs,
      imageError: null,
      fileInputRef: { current: null },
      onFileInputChange() {},
      onRemoveImage(id) { removed.push(id); },
      onRemoveDoc(id) { removed.push(id); },
      onDismissError() {},
    }));
  });
  const items = [...host.querySelectorAll("[data-attachment-id]")];
  assert.deepEqual(items.map((node) => node.getAttribute("data-attachment-id")), [
    "report", "shot", "broken",
  ]);
  assert.equal(items[0].getAttribute("data-attachment-state"), "loading");
  assert.equal(items[2].getAttribute("data-attachment-state"), "error");
  assert.match(items[1].textContent, /PNG/);
  assert.match(items[1].textContent, /1\.3 MB/);
  assert.match(items[2].textContent, /PDF/, "known MIME supplies the format when extension is absent");
  const preview = items[1].querySelector("[data-attachment-preview]");
  const remove = items[1].querySelector("[data-attachment-remove]");
  assert.ok(preview);
  assert.ok(remove);
  assert.notEqual(preview, remove);
  remove.dispatchEvent(new window.Event("click", { bubbles: true }));
  assert.deepEqual(removed, ["shot"]);
  await act(async () => { root.unmount(); });
  host.remove();
  rmSync(dir, { recursive: true, force: true });
});

test("submit keeps draft when an attachment is loading or failed", async () => {
  const { setSocket } = await import("../../lib/runtime-bridge/state.ts");
  const frames = [];
  setSocket({
    readyState: 1,
    send(raw) { frames.push(JSON.parse(raw)); },
  });
  const { useSessionStore } = await import("../../lib/session-store/index.ts");
  useSessionStore.setState({
    currentSessionId: "local_attach-submit",
    activeChatKey: "local_attach-submit",
    composerDrafts: { "local_attach-submit": "summarize these" },
  });

  let hook;
  function Probe({ images, docs }) {
    hook = useChatSubmit({
      bound: null,
      input: "summarize these",
      activeChatKey: "local_attach-submit",
      currentSessionId: "local_attach-submit",
      isRunning: false,
      noEnabledModels: false,
      promptNeedModel() {},
      send() { return true; },
      setComposerInputFor() { throw new Error("draft must stay"); },
      setHistoryIndex() {},
      slash: { runCommand() { return false; }, close() {} },
      pendingImages: images,
      pendingDocs: docs,
      clearAttachmentsAfterSubmit() { throw new Error("attachments must stay"); },
      thinking: "medium",
      toolsEnabled: true,
      toolsProfile: "__agent__",
      webSearchEnabled: false,
      fastEnabled: false,
      fastSupported: false,
      runningMessageMode: "queue",
      dispatchFunction() { return false; },
    });
    useEffect(() => {}, [images, docs]);
    return null;
  }

  const host = document.createElement("div");
  document.body.appendChild(host);
  const root = createRoot(host);
  await act(async () => {
    root.render(createElement(Probe, {
      images: [imageItem("shot", 0, { loading: true, data: "" })],
      docs: [],
    }));
  });
  await act(async () => { await hook.submit(); });
  assert.equal(frames.length, 0, "loading attachment must not send");

  await act(async () => {
    root.render(createElement(Probe, {
      images: [],
      docs: [docItem("report", 0, { error: "Read failed" })],
    }));
  });
  await act(async () => { await hook.submit(); });
  assert.equal(frames.length, 0, "failed attachment must not send");
  await act(async () => { root.unmount(); });
  host.remove();
});
