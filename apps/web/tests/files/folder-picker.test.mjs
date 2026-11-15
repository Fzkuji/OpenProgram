import assert from "node:assert/strict";
import test from "node:test";

import { installLoader } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  replace: {
    "/components/ui/dialog.tsx": "export const Dialog = ({ open, children }) => (open ? children : null); export const DialogContent = ({ children }) => children; export const DialogHeader = DialogContent; export const DialogTitle = DialogContent; export const DialogDescription = DialogContent; export const DialogFooter = DialogContent;",
    "/components/ui/button.tsx": "import { createElement } from 'react'; export const Button = ({ variant, size, ...props }) => createElement('button', props);",
  },
});
installDom();

const {
  FolderPickerRequestError,
  requestNativeFolder,
  validateManualFolder,
} = await import("../../lib/projects/folder-picker.ts");

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

test("a host without a native picker asks for a server path and POSTs it for validation", async () => {
  const { createElement: h } = await import("react");
  const { useFolderPicker } = await import("../../components/ui/folder-picker.tsx");
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, method: init.method, body });
    return body.manual_path
      ? response(200, { path: "/srv/normalized", unsupported: false })
      : response(200, { path: null, unsupported: true });
  };
  let picker;
  function Probe() {
    picker = useFolderPicker();
    return picker.folderPickerDialog;
  }
  const view = await render(h(Probe));
  try {
    let picked;
    await view.act(() => { picked = picker.pickFolder("/srv/start"); });
    assert.equal(picker.manualOpen, true);
    assert.match(view.container.textContent, /Open folder on server/);
    const input = view.container.querySelector("input");
    assert.equal(input.value, "/srv/start", "the dialog starts from the requested folder");

    input.type = "text"; // React only tracks input value changes on typed inputs; linkedom has no default type.
    Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), "value").set.call(input, "  ~/project  ");
    await view.act(() => input.dispatchEvent(new Event("input", { bubbles: true })));
    await view.act(() => view.container.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));

    assert.equal(await picked, "/srv/normalized");
    assert.deepEqual(calls.at(-1), { url: "/api/pick-folder", method: "POST", body: { manual_path: "~/project" } });
    assert.equal(picker.manualOpen, false);
  } finally {
    globalThis.fetch = realFetch;
    await view.unmount();
  }
});
