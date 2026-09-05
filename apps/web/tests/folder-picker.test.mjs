import assert from "node:assert/strict";
import test from "node:test";

import {
  FolderPickerRequestError,
  requestNativeFolder,
  validateManualFolder,
} from "../lib/folder-picker.ts";

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; },
  };
}

test("native picker uses POST and preserves unsupported vs cancel", async () => {
  const calls = [];
  const unsupported = await requestNativeFolder("/srv/project", async (...args) => {
    calls.push(args);
    return response(200, { path: null, unsupported: true });
  });

  assert.deepEqual(unsupported, { path: null, unsupported: true });
  assert.equal(calls[0][0], "/api/pick-folder");
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[0][1].body), { start: "/srv/project" });

  const cancelled = await requestNativeFolder("", async () =>
    response(200, { path: null, unsupported: false }));
  assert.equal(cancelled.unsupported, false);
});

test("manual path is validated and normalized by the worker", async () => {
  let body = null;
  const path = await validateManualFolder("~/project", async (_url, init) => {
    body = JSON.parse(init.body);
    return response(200, { path: "/home/tester/project", unsupported: false });
  });

  assert.deepEqual(body, { manual_path: "~/project" });
  assert.equal(path, "/home/tester/project");
});

test("manual path validation exposes a typed error", async () => {
  await assert.rejects(
    validateManualFolder("relative", async () =>
      response(400, { path: null, error: "folder path must be absolute" })),
    (error) => {
      assert.ok(error instanceof FolderPickerRequestError);
      assert.equal(error.kind, "invalid-path");
      assert.match(error.message, /must be absolute/);
      return true;
    },
  );
});
