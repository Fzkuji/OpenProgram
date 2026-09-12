import test from "node:test";
import assert from "node:assert/strict";
import { fileCapabilities, fileExtension } from "../lib/documents/file-formats.ts";

test("classification uses the final filename, including worker-side Windows paths", () => {
  assert.equal(fileExtension("C:\\workspace.pdf\\file.PSD"), "psd");
  assert.equal(fileCapabilities("C:\\workspace.pdf\\file.PSD").preview, "layered-image");
  assert.equal(fileCapabilities("reports.pdf/file").preview, "download");
  assert.equal(fileCapabilities("report.docx.exe").textEditable, false);
});

test("text editing and passive format previews remain distinct capabilities", () => {
  assert.equal(fileCapabilities(".gitignore").textEditable, true);
  assert.equal(fileCapabilities("README").textEditable, true);
  assert.equal(fileCapabilities("diagram.svg").textEditable, false);
  assert.equal(fileCapabilities("slides.pptx").textEditable, false);
});

test("decoder rejects advertised oversized input before consuming its stream", async () => {
  const { readPreviewBytes, MAX_PREVIEW_BYTES } = await import("../lib/documents/read-preview-bytes.ts");
  const originalFetch = globalThis.fetch;
  let cancelled = false;
  globalThis.fetch = async () => new Response(new ReadableStream({ cancel() { cancelled = true; } }), {
    headers: { "content-length": String(MAX_PREVIEW_BYTES + 1) },
  });
  try {
    await assert.rejects(readPreviewBytes("https://preview.test/file", new AbortController().signal), /PREVIEW_RESOURCE_LIMIT/);
    assert.equal(cancelled, true);
  } finally { globalThis.fetch = originalFetch; }
});

test("decoder cancels oversized chunked input with no advertised length", async () => {
  const { readPreviewBytes, MAX_PREVIEW_BYTES } = await import("../lib/documents/read-preview-bytes.ts");
  const originalFetch = globalThis.fetch;
  let cancelled = false;
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new Uint8Array(MAX_PREVIEW_BYTES + 1)); },
    cancel() { cancelled = true; },
  }));
  try {
    await assert.rejects(readPreviewBytes("https://preview.test/file", new AbortController().signal), /PREVIEW_RESOURCE_LIMIT/);
    assert.equal(cancelled, true);
  } finally { globalThis.fetch = originalFetch; }
});
