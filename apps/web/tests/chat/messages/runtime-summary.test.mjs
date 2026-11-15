// The one-line summary on a Function card, and which runtime runs nest under
// an assistant reply when a transcript is loaded.
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { runtimeSummaryLabel } from "../../../components/chat/messages/runtime/runtime-summary.ts";

const startedAt = 1_700_000_000;
const zh = (_en, cn) => cn;
const gui = (output, extra = {}) => runtimeSummaryLabel({
  fnName: "gui_agent", status: "completed", tree: { duration_ms: 225, output }, ...extra,
});

describe("runtime summary label", () => {
  test("a running run shows elapsed time and step count", () => {
    assert.equal(
      runtimeSummaryLabel({
        fnName: "report_workflow", status: "running", timestamp: startedAt,
        now: (startedAt + 130) * 1000, tree: { name: "root", children: [{ name: "read_file" }] },
      }),
      "report_workflow · Running… · 02:10 · 2 steps",
    );
  });

  test("terminal runs show duration and the output, the error, or the step count", () => {
    assert.equal(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "completed",
        tree: { duration_ms: 166_000, output: " 2 files\nprocessed " } }),
      "report_workflow · Completed · 02:46 · 2 files processed",
    );
    assert.equal(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "error",
        tree: { duration_ms: 82_000, error: "Permission denied" } }),
      "report_workflow · Error · 01:22 · Permission denied",
    );
    assert.equal(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "cancelled",
        tree: { duration_ms: 42_000, error: "cancelled by user", children: [{}, {}] } }),
      "report_workflow · Cancelled · 00:42 · 3 steps",
    );
  });

  test("the live outer status wins over a stale terminal payload", () => {
    const stale = JSON.stringify({ status: "failed", success: false, summary: "stale terminal payload" });
    assert.match(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "running", tree: { output: stale } }),
      /^report_workflow · Running… ·/,
    );
    assert.match(
      runtimeSummaryLabel({ fnName: "gui_agent", status: "running", tree: { output: stale } }),
      /^gui_agent · Running… ·/,
    );
  });

  test("the GUI task outcome replaces the internal completed transport status", () => {
    assert.equal(
      gui({ status: "succeeded", success: false, summary: "The Page title is OpenProgram." }),
      "gui_agent · Succeeded · 00:00 · The Page title is OpenProgram.",
    );
    assert.equal(
      gui(JSON.stringify({ status: "failed", success: true, summary: "No accessible built-in Page is open." })),
      "gui_agent · Failed · 00:00 · No accessible built-in Page is open.",
    );
    assert.equal(
      gui(JSON.stringify({ status: "infeasible", success: true, summary: "Login is required.",
        handoff_instruction: "Sign in, then retry this GUI task." })),
      "gui_agent · Needs takeover · 00:00 · Sign in, then retry this GUI task.",
    );
  });

  test("a returned GUI cancellation stays distinct from failure and error", () => {
    assert.match(gui(JSON.stringify({ status: "cancelled", success: false })), /^gui_agent · Cancelled · 00:00 ·/);
  });

  test("a terminal GUI node without a valid task outcome shows Error, not Completed", () => {
    for (const output of ['{"status":', { status: "mystery" }, { success: false }]) {
      assert.match(
        runtimeSummaryLabel({ fnName: "gui_agent", status: "completed", tree: { output } }),
        /^gui_agent · Error ·/,
      );
    }
  });

  test("a runtime exception or stored tree error wins over a returned GUI outcome", () => {
    assert.match(
      runtimeSummaryLabel({ fnName: "gui_agent", status: "error", tree: { error: "Worker crashed",
        output: JSON.stringify({ status: "succeeded", success: true, summary: "stale successful payload" }) } }),
      /^gui_agent · Error · .*Worker crashed$/,
    );
    assert.match(
      runtimeSummaryLabel({ fnName: "gui_agent", status: "completed", tree: { error: "Persisted runner error",
        output: { status: "succeeded", success: true, summary: "stale success" } } }),
      /^gui_agent · Error · .*Persisted runner error$/,
    );
  });

  test("other functions treat a status field as data and ignore GUI-only outer statuses", () => {
    assert.match(
      runtimeSummaryLabel({ fnName: "status_probe", status: "completed",
        tree: { output: JSON.stringify({ status: "failed" }) } }),
      /^status_probe · Completed ·/,
    );
    for (const fnName of ["status_probe", "report_workflow"]) {
      for (const status of ["succeeded", "infeasible"]) {
        assert.match(
          runtimeSummaryLabel({ fnName, status, tree: { output: "business result" } }),
          new RegExp(`^${fnName} · Completed ·`),
        );
      }
    }
  });

  test("labels follow the interface language", () => {
    assert.equal(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "running", timestamp: startedAt,
        now: (startedAt + 8) * 1000, tree: { name: "root", children: [{ name: "read_file" }] }, text: zh }),
      "report_workflow · 运行中… · 00:08 · 2 步",
    );
    assert.equal(
      runtimeSummaryLabel({ fnName: "report_workflow", status: "completed", tree: { duration_ms: 12_000 }, text: zh }),
      "report_workflow · 已完成 · 00:12 · 1 步",
    );
    for (const [status, summary, expected] of [
      ["succeeded", "已确认页面标题。", "gui_agent · 成功 · 00:00 · 已确认页面标题。"],
      ["failed", "没有可访问的内置页面。", "gui_agent · 失败 · 00:00 · 没有可访问的内置页面。"],
      ["infeasible", "需要登录。", "gui_agent · 需要接手 · 00:00 · 请先登录。"],
    ]) {
      assert.equal(
        gui({ status, success: status === "succeeded", summary,
          ...(status === "infeasible" ? { handoff_instruction: "请先登录。" } : {}) }, { text: zh }),
        expected,
      );
    }
  });
});

// ── The Function card itself, rendered. Loaded lazily so the pure tests
// above run without a DOM.
const card = (async () => {
  const { installLoader } = await import("../../_support/load.mjs");
  const { installDom, render } = await import("../../_support/dom.mjs");
  installLoader();
  // The real i18n module answers in English through the stored locale.
  installDom({ pathname: "/chat" });
  globalThis.WebSocket ??= { OPEN: 1 };
  const { createElement } = await import("react");
  const { RuntimeBlock } = await import("../../../components/chat/messages/runtime/runtime-block.tsx");
  const { useSessionStore } = await import("../../../lib/session-store/index.ts");
  const { setSocket } = await import("../../../lib/runtime-bridge/state.ts");
  const { settleFunctionRetry } = await import("../../../lib/runtime-bridge/actions/function-retry.ts");
  return { createElement, render, RuntimeBlock, useSessionStore, setSocket, settleFunctionRetry };
})();

describe("RuntimeBlock", () => {
  const run = (overrides = {}) => ({
    id: "run-1", role: "assistant", display: "runtime", function: "report_workflow", status: "completed",
    timestamp: startedAt, content: "",
    contextTree: { path: "run-1", name: "report_workflow", status: "completed", duration_ms: 2_000, output: "**2 files** processed",
      children: [{ path: "c1", name: "read_file", status: "completed", duration_ms: 5 }] },
    ...overrides,
  });

  test("a top-level run is the ƒ avatar, the summary strip, then the reply, then the footer", async () => {
    const { createElement, render, RuntimeBlock, useSessionStore } = await card;
    useSessionStore.setState({ currentSessionId: "rb" });
    const view = await render(createElement(RuntimeBlock, { msg: run() }));
    try {
      const host = view.container.querySelector(".runtime-program-run");
      assert.equal(host.querySelector(".runtime-program-avatar").textContent, "ƒ");
      const content = host.querySelector(".runtime-program-content");
      assert.deepEqual([...content.children].map((el) => el.className.split(" ")[0]),
        ["tl", "runtime-program-conclusion", "message-actions-footer"]);
      assert.match(content.querySelector(".tl-label").textContent, /^report_workflow · Completed · 00:02 ·/);
      assert.ok(content.querySelector("[data-function-answer] strong"));
      // One keyboard-focusable timestamp; actions carry an accessible name, not a native title.
      const stamp = content.querySelector(".runtime-actions-footer .message-timestamp");
      assert.equal(stamp.getAttribute("tabindex"), "0");
      assert.ok(stamp.getAttribute("aria-label"));
      const actions = [...content.querySelectorAll(".runtime-actions-footer button")];
      assert.deepEqual(actions.map((b) => b.getAttribute("aria-label")), ["Copy result", "Retry", "Edit and re-run"]);
      assert.ok(actions.every((b) => !b.hasAttribute("title")));
      // The tree opens its first level only once the strip is expanded.
      await view.act(() => content.querySelector(".tl-toggle").click());
      const heads = [...content.querySelectorAll(".tl-step-head")];
      assert.equal(heads[0].getAttribute("aria-expanded"), "true");
    } finally { await view.unmount(); }
  });

  test("Retry asks for this exact code node to run again", async () => {
    const { createElement, render, RuntimeBlock, useSessionStore, setSocket, settleFunctionRetry } = await card;
    const sent = [];
    setSocket({ readyState: 1, send: (frame) => sent.push(JSON.parse(frame)) });
    useSessionStore.setState({ currentSessionId: "rb", rowsByView: { rb: { "run-1": run() } }, messageOrder: { rb: ["run-1"] } });
    const view = await render(createElement(RuntimeBlock, { msg: run() }));
    try {
      const retry = view.container.querySelector('button[aria-label="Retry"]');
      await view.act(() => retry.click());
      assert.equal(sent.length, 1);
      assert.equal(sent[0].action, "retry_function");
      assert.equal(sent[0].session_id, "rb");
      assert.equal(sent[0].function, "report_workflow");
      assert.equal(sent[0].node_id, "run-1");
      assert.equal(typeof sent[0].retry_request_id, "string");
      settleFunctionRetry("rb", sent[0].retry_request_id);
    } finally { await view.unmount(); setSocket(null); }
  });

  test("a call the model made itself keeps the frameless tree, a timestamp and no Retry", async () => {
    const { createElement, render, RuntimeBlock, useSessionStore } = await card;
    useSessionStore.setState({ currentSessionId: "rb" });
    const view = await render(createElement(RuntimeBlock, { msg: run(), nested: true }));
    try {
      const tl = view.container.querySelector(".tl");
      assert.equal(tl.getAttribute("data-open"), "1");
      assert.equal(view.container.querySelector(".runtime-program-run"), null);
      assert.ok(tl.querySelector(".message-actions-footer .message-timestamp"));
      assert.equal(view.container.querySelector('button[aria-label="Retry"]'), null);
    } finally { await view.unmount(); }
  });
});
