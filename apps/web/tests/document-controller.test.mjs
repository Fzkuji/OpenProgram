import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";

const root = new URL("../", import.meta.url);
registerHooks({ resolve(specifier, context, nextResolve) {
  if (specifier.startsWith("@/")) {
    const base = new URL(specifier.slice(2), root);
    for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
      const url = `${base.href}${suffix}`;
      if (existsSync(fileURLToPath(url))) return { url, shortCircuit: true };
    }
  }
  return nextResolve(specifier, context);
} });

const { DocumentController } = await import("../lib/state/document-controller.ts");

test("controller publishes an edited text snapshot through the shared document API", async () => {
  const requests = [];
  const controller = new DocumentController({ projectId: "p", path: "notes.md", fetchImpl: async (url, init) => { requests.push({ url, init }); return new Response(JSON.stringify({ revision: "rev-2", mtime: 2 }), { status: 200 }); } });
  controller.hydrate({ bytes: "old", revision: "rev-1" });
  controller.update("new");
  await controller.flush();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].init.method, "PUT");
  assert.equal(new TextDecoder().decode(await requests[0].init.body.arrayBuffer()), "new");
  await controller.close();
});

test("a newer edit keeps the submitted baseline and schedules an ordered second write", async () => {
  let resolveFirst;
  const requests = [];
  const controller = new DocumentController({ projectId: "p", path: "notes.md", fetchImpl: async (url, init) => {
    requests.push({ url, init });
    if (requests.length === 1) await new Promise((resolve) => { resolveFirst = resolve; });
    return new Response(JSON.stringify({ revision: `rev-${requests.length}`, mtime: requests.length }), { status: 200 });
  }, debounceMs: 100, maxDebounceMs: 1000 });
  await controller.hydrate({ bytes: "old", revision: "rev-1" });
  controller.update("first");
  const firstFlush = controller.flush();
  controller.update("second");
  await new Promise((resolve) => setTimeout(resolve, 20));
  resolveFirst();
  await firstFlush;
  await controller.flush();
  assert.equal(requests.length, 2);
  assert.equal(await requests[1].init.body.text(), "second");
  assert.equal(requests[1].init.headers["x-baseline-revision"], "rev-1");
  await controller.close();
});

test("failed flush keeps the controller open and draft available", async () => {
  const controller = new DocumentController({ projectId: "p", path: "notes.md", fetchImpl: async () => { throw new Error("offline"); } });
  await controller.hydrate({ bytes: "old", revision: "rev-1" });
  controller.update("draft");
  await assert.rejects(controller.flush(), /offline|save failed/i);
  assert.equal(controller.getState().status, "error");
  assert.equal(await controller.getState().draft.text(), "draft");
});
