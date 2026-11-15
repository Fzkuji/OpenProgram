// The execution timeline under a reply: the summary toggle, step rows (icon,
// title link, copy, expansion) and sub-agent rows.
import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    // Icons render as a marker carrying the glyph name and motion preset.
    "@/components/solar-icons": `import { createElement } from "react";
      export const SolarIcon = ({ name, motionPreset }) => createElement("i", { "data-glyph": name, "data-motion": motionPreset });`,
    "@/lib/clipboard": "export const copyText = async (value) => { (globalThis.__copied ??= []).push(value); return true; };",
  },
});
// The real i18n module answers in English through the stored locale.
installDom({ pathname: "/chat" });
const { createElement: h } = await import("react");
const { ExecutionStrip, StepRow, SubAgentStep, TreeStep } = await import(
  "../../../components/chat/messages/execution/execution-strip.tsx"
);
const { useSessionStore } = await import("../../../lib/session-store/index.ts");
const { setDefaultTranscriptView } = await import("../../../lib/prefs/transcript-view.ts");

let view;
afterEach(async () => {
  await view?.unmount();
  view = undefined;
  setDefaultTranscriptView("normal");
});
const $ = (selector) => view.container.querySelector(selector);
const $$ = (selector) => [...view.container.querySelectorAll(selector)];

describe("ExecutionStrip", () => {
  test("starts collapsed in the normal view, also while streaming, and the toggle opens it", async () => {
    view = await render(h(ExecutionStrip, { label: "2 steps", streaming: true }, h("div", { id: "step" })));
    const toggle = $(".tl-toggle");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    assert.equal($(".tl").getAttribute("data-open"), "0");
    assert.equal($("#step"), null);
    await view.act(() => toggle.click());
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.equal($(".tl").getAttribute("data-open"), "1");
    assert.ok($("#step"));
  });

  test("starts open in the verbose view, and a manual choice wins over the view", async () => {
    setDefaultTranscriptView("verbose");
    view = await render(h(ExecutionStrip, { label: "run" }, h("div", { id: "step" })));
    assert.equal($(".tl-toggle").getAttribute("aria-expanded"), "true");
    await view.act(() => $(".tl-toggle").click());
    await view.act(() => setDefaultTranscriptView("verbose"));
    assert.equal($(".tl-toggle").getAttribute("aria-expanded"), "false");
  });

  test("exposes the sub-agent heads it owns so a return can reopen it", async () => {
    view = await render(h(ExecutionStrip, { label: "run", subagentHeads: ["h1", undefined, "h2"] }, null));
    assert.equal($(".tl").getAttribute("data-subagent-heads"), "h1 h2");
  });
});

describe("StepRow", () => {
  test("generic rows fall back to static Solar glyphs per kind", async () => {
    view = await render(h("div", null,
      h(StepRow, { icon: "function", title: "fn" }),
      h(StepRow, { icon: "llm", title: "LLM" }),
      h(StepRow, { icon: "thinking", title: "Thinking" }),
      h(StepRow, { icon: "subagent", title: "Agent" }),
    ));
    assert.deepEqual($$(".tl-step-icon i").map((icon) => icon.getAttribute("data-glyph")),
      ["sledgehammer", "cpu", "lightbulb-bolt", "ghost"]);
    assert.ok($$(".tl-step-icon i").every((icon) => icon.getAttribute("data-motion") === "none"));
  });

  test("only expandable rows mark their icon toggleable and report aria-expanded", async () => {
    view = await render(h("div", null,
      h(StepRow, { icon: "function", title: "leaf" }),
      h(StepRow, { icon: "function", title: "parent", subSteps: h("span", null, "kid") }),
    ));
    const [leaf, parent] = $$(".tl-step-head");
    assert.equal(leaf.hasAttribute("aria-expanded"), false);
    assert.equal(leaf.querySelector(".tl-step-icon").classList.contains("is-toggleable"), false);
    assert.equal(parent.getAttribute("aria-expanded"), "false");
    assert.ok(parent.querySelector(".tl-step-icon").classList.contains("is-toggleable"));
  });

  test("the title opens details; a click elsewhere on the row only toggles", async () => {
    const shown = [];
    const original = useSessionStore.getState().showDetail;
    useSessionStore.setState({ showDetail: (detail) => shown.push(detail.path) });
    try {
      view = await render(h(StepRow, {
        icon: "function", title: "read_file", detail: { path: "node-1", name: "read_file" },
        subSteps: h("span", { className: "kid" }, "kid"),
      }));
      await view.act(() => $(".tl-step-head").click());
      assert.deepEqual(shown, []);
      assert.equal($(".tl-step-head").getAttribute("aria-expanded"), "true");
      await view.act(() => $(".tl-step-title").click());
      assert.deepEqual(shown, ["node-1"]);
      assert.equal($(".tl-step-head").getAttribute("aria-expanded"), "true");
    } finally { useSessionStore.setState({ showDetail: original }); }
  });

  test("every row copies a deterministic value, with Copy after meta and before row actions", async () => {
    globalThis.__copied = [];
    view = await render(h(StepRow, {
      icon: "function", title: "grep", target: "src/", note: "3 matches",
      meta: h("time", null, "10:00"), actions: h("button", { className: "row-action" }, "Open"),
    }));
    const act = [...$(".tl-step-act").children];
    assert.deepEqual(act.map((node) => node.tagName.toLowerCase() + "." + (node.className || "")),
      ["time.", "button.tl-btn", "button.row-action"]);
    await view.act(async () => { act[1].click(); await Promise.resolve(); });
    assert.deepEqual(globalThis.__copied, ["grep · src/ · 3 matches"]);
  });
});

describe("TreeStep", () => {
  test("defaultKidsOpen opens only the first level of a workflow tree", async () => {
    const tree = {
      path: "root", name: "workflow", status: "completed", duration_ms: 10,
      children: [{ path: "child", name: "step", status: "completed", duration_ms: 5,
        children: [{ path: "grandchild", name: "leaf", status: "completed", duration_ms: 1 }] }],
    };
    view = await render(h(TreeStep, { node: tree, defaultKidsOpen: true }));
    const heads = $$(".tl-step-head");
    assert.deepEqual(heads.map((head) => head.querySelector(".tl-step-title").textContent), ["workflow", "step"]);
    assert.equal(heads[0].getAttribute("aria-expanded"), "true");
    assert.equal(heads[1].getAttribute("aria-expanded"), "false");
  });
});

describe("SubAgentStep", () => {
  test("is a timeline row carrying its branch head and its own timestamp", async () => {
    view = await render(h(SubAgentStep, { card: {
      id: "card-1", role: "assistant", content: "", timestamp: 1_700_000_000_000,
      attach: { session_id: "s1", head_id: "head-9", label: "probe", status: "completed" },
    } }));
    const row = $(".tl-step");
    assert.equal(row.getAttribute("data-head-id"), "head-9");
    assert.equal(row.getAttribute("data-msg-id"), "card-1");
    assert.equal($(".tl-step-title").textContent, "Sub-agent: probe");
    assert.ok($(".tl-step-act .message-timestamp"));
  });
});
