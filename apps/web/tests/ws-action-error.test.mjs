import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

const WEB_ROOT = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier.startsWith("@/")) {
      const base = new URL(specifier.slice(2), WEB_ROOT).href;
      const file = `${base}.ts`;
      const url = existsSync(fileURLToPath(file)) ? file : `${base}/index.ts`;
      return { url, shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
});

const { actionErrorNotice, consumeActionError } = await import(
  "../lib/net/action-error.ts"
);

test("handler failures are not reported as missing backend handlers", () => {
  const notice = actionErrorNotice({
    action: "save_settings",
    code: "handler_error",
    error: "secret=/private/credential",
  });

  assert.equal(notice.code, "handler_error");
  assert.equal(notice.en, "Action save_settings failed");
  assert.equal(notice.zh, "操作 save_settings 失败");
  assert.doesNotMatch(JSON.stringify(notice), /Unknown action|credential/);
});

test("legacy code-less and current unknown-action frames keep unknown wording", () => {
  for (const data of [
    { action: "removed_action" },
    { action: "removed_action", code: "unknown_action" },
  ]) {
    const notice = actionErrorNotice(data);
    assert.equal(notice.code, "unknown_action");
    assert.equal(notice.en, "Unknown action removed_action — no backend handler");
    assert.equal(notice.zh, "未知操作 removed_action — 后端没有对应处理器");
  }
});

test("unrecognized codes use a safe generic failure", () => {
  const notice = actionErrorNotice({
    action: "future_action",
    code: "future_failure",
    error: "token=do-not-display",
  });

  assert.equal(notice.code, "future_failure");
  assert.equal(notice.en, "Action future_action failed");
  assert.equal(notice.zh, "操作 future_action 失败");
  assert.doesNotMatch(JSON.stringify(notice), /Unknown action|do-not-display/);
});

test("production consumer emits classified low-sensitivity error toasts", () => {
  const { window } = parseHTML("<!doctype html><html><body></body></html>");
  globalThis.window = window;
  globalThis.CustomEvent = window.CustomEvent;
  const toasts = [];
  const logs = [];
  window.addEventListener("op:toast", (event) => toasts.push(event.detail));
  const originalError = console.error;
  console.error = (...args) => logs.push(args);
  try {
    for (const data of [
      { action: "bad", code: "handler_error", error: "secret-handler" },
      { action: "old_missing", error: "secret-legacy" },
      { action: "missing", code: "unknown_action", error: "secret-current" },
      { action: "future", code: "future_failure", error: "secret-future" },
    ]) {
      consumeActionError(data, (en) => en);
    }
  } finally {
    console.error = originalError;
  }

  assert.deepEqual(
    toasts.map(({ message, tone }) => ({ message, tone })),
    [
      { message: "Action bad failed", tone: "error" },
      { message: "Unknown action old_missing — no backend handler", tone: "error" },
      { message: "Unknown action missing — no backend handler", tone: "error" },
      { message: "Action future failed", tone: "error" },
    ],
  );
  assert.doesNotMatch(JSON.stringify({ toasts, logs }), /secret-/);
});
