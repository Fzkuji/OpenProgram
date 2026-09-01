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

const {
  actionErrorNotice,
  consumeActionError,
  consumeCommandErrorFrame,
  operationErrorNotice,
  consumeOperationError,
} = await import(
  "../lib/net/action-error.ts"
);

test("operation errors preserve safe correlation metadata", () => {
  const notice = operationErrorNotice({
    action: "save_settings",
    code: "handler_error",
    request_id: "request-1",
    session_id: "s1",
    retryable: false,
    message: "secret=/private/credential",
  });

  assert.equal(notice.requestId, "request-1");
  assert.equal(notice.sessionId, "s1");
  assert.equal(notice.retryable, false);
  assert.doesNotMatch(JSON.stringify(notice), /credential/);
});

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

test("legacy and current envelopes normalize to the same notice", () => {
  assert.deepEqual(
    actionErrorNotice({ action: "bad", code: "handler_error" }),
    operationErrorNotice({ action: "bad", code: "handler_error" }),
  );
});

test("legacy envelopes cannot inject unsafe correlation metadata", () => {
  for (const invalid of ["line\nbreak", "line\u0085break", "x".repeat(129)]) {
    const notice = actionErrorNotice({
      action: invalid,
      session_id: invalid,
      request_id: invalid,
      code: "handler_error",
      error: "secret-legacy",
    });
    assert.equal(notice.action, "?");
    assert.equal(notice.sessionId, undefined);
    assert.equal(notice.requestId, undefined);
    assert.doesNotMatch(JSON.stringify(notice), /secret|line|xxxx/);
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
    for (const [consumer, data] of [
      [consumeOperationError, { action: "bad", code: "handler_error", message: "secret-handler" }],
      [consumeActionError, { action: "old_missing", error: "secret-legacy" }],
      [consumeOperationError, { action: "missing", code: "unknown_action", message: "secret-current" }],
      [consumeOperationError, { action: "future", code: "future_failure", message: "secret-future" }],
    ]) {
      consumer(data, (en) => en);
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

test("WebSocket dispatch boundary consumes current and legacy error frames", () => {
  const { window } = parseHTML("<!doctype html><html><body></body></html>");
  globalThis.window = window;
  globalThis.CustomEvent = window.CustomEvent;
  const toasts = [];
  const logs = [];
  window.addEventListener("op:toast", (event) => toasts.push(event.detail));
  const originalError = console.error;
  console.error = (...args) => logs.push(args);
  try {
    assert.equal(consumeCommandErrorFrame({ type: "pong" }, (en) => en), false);
    assert.equal(
      consumeCommandErrorFrame({
        type: "operation_error",
        data: {
          action: "save_settings",
          code: "handler_error",
          message: "secret-current",
        },
      }, (en) => en),
      true,
    );
    assert.equal(
      consumeCommandErrorFrame({
        type: "action_error",
        data: { action: "save_settings", code: "handler_error", error: "secret-legacy" },
      }, (en) => en),
      true,
    );
  } finally {
    console.error = originalError;
  }

  assert.deepEqual(
    toasts.map(({ message, tone }) => ({ message, tone })),
    [
      { message: "Action save_settings failed", tone: "error" },
      { message: "Action save_settings failed", tone: "error" },
    ],
  );
  assert.doesNotMatch(JSON.stringify({ toasts, logs }), /secret-/);
});
