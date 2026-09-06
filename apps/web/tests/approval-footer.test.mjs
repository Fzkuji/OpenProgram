import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../", import.meta.url);
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/runtime-bridge/state") return { url: "data:text/javascript,export const getSocket = () => globalThis.approvalSocket", shortCircuit: true };
    if (specifier.endsWith(".module.css")) {
      return { url: "data:text/javascript,export default {}", shortCircuit: true };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href : null;
    if (base) {
      for (const suffix of [".ts", ".tsx", "/index.ts", "/index.tsx"]) {
        if (existsSync(fileURLToPath(base + suffix))) {
          return { url: base + suffix, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith(".tsx")) {
      return {
        format: "module", shortCircuit: true,
        source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
          compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});
const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
// Text assertions select a browser preference, independent of the host OS.
globalThis.localStorage = { getItem(key) { return key === "agentic_locale" ? "en" : null; }, setItem() {}, removeItem() {} };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => setTimeout(() => callback(0), 0);
globalThis.cancelAnimationFrame = clearTimeout;
window.location = { pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
let respond;
globalThis.fetch = (...args) => respond(...args);
const { act, createElement, useState, useRef } = await import("react");
const { createRoot } = await import("react-dom/client");
const { QuestionMode } = await import("../components/chat/composer/modes/question/question-mode.tsx");
globalThis.WebSocket = { OPEN: 1 };
const decision = { id: "wait-one", kind: "approval", prompt: "Allow this command?", detail: "echo test", options: [], multi: false, allow_custom: false, executionId: "exec-one", expectedVersion: 3, waitGeneration: 0 };
async function mounted(q, check) {
  const frames = [], resolved = [], discussed = [];
  globalThis.approvalSocket = { readyState: 1, send: value => frames.push(JSON.parse(value)) };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(QuestionMode, { decision: q, onResolve: id => resolved.push(id), onChatAbout: () => discussed.push(q.id) })));
    const button = label => [...host.querySelectorAll("button")].find(b => b.textContent.replace("✓ ", "") === label);
    await check({ host, button, frames, resolved, discussed });
  } finally { await act(async () => root.unmount()); host.remove(); }
}

test("approval choices and discussion share the bottom action row with Send", async () => {
  await mounted(decision, async ({ host, button, frames, discussed }) => {
    const footer = host.querySelector('[aria-label="Decision actions"]');
    assert.ok(footer, "one shared bottom action row");
    for (const label of ["Allow once", "Always allow", "Deny", "Chat about this", "Send"]) assert.ok(footer.contains(button(label)), label);
    assert.equal(button("Chat about this").nextElementSibling, button("Send"));
    assert.equal(button("Send").disabled, true);
    await act(async () => button("Allow once").click());
    assert.equal(frames.length, 0);
    assert.equal(button("Allow once").getAttribute("aria-pressed"), "true");
    const enter = new Event("keydown", { bubbles: true, cancelable: true });
    Object.defineProperty(enter, "key", { value: "Enter" });
    await act(async () => button("Chat about this").dispatchEvent(enter));
    assert.equal(enter.defaultPrevented, false, "native button activation must not submit the selected approval");
    assert.equal(frames.length, 0);
    await act(async () => button("Chat about this").click());
    assert.deepEqual(discussed, ["wait-one"]);
    assert.equal(frames.length, 0);
  });
});
for (const [label, scope] of [["Allow once", "once"], ["Always allow", "always"], ["Deny", null], ["Always allow this path", "always_path"]]) {
  test(`${label} only sends its original outcome on Send`, async () => {
    const q = scope === "always_path" ? { ...decision, args: { _sandbox_escalation: { from: "sandbox", to: "host", path: "/tmp/test" } } } : decision;
    await mounted(q, async ({ button, frames, resolved }) => {
      await act(async () => button(label).click());
      assert.equal(frames.length, 0);
      await act(async () => button("Send").click());
      assert.equal(frames.length, 1);
      assert.equal(frames[0].action, scope ? "execution.wait.answer" : "execution.wait.decline");
      assert.equal(frames[0].execution_id, "exec-one");
      assert.equal(frames[0].expected_version, 3);
      if (scope) assert.deepEqual(frames[0].payload.answer, { answer: "approve", scope });
      assert.deepEqual(resolved, ["wait-one"]);
    });
  });
}
test("multiple questions retain navigation and ordered answers", async () => {
  await mounted({ ...decision, kind: "ask_many", questions: [
    { prompt: "First", options: ["One"], multi: false, allow_custom: false },
    { prompt: "Second", options: ["Two"], multi: false, allow_custom: false },
  ] }, async ({ button, frames }) => {
    await act(async () => button("One").click());
    await act(async () => button("Next ›").click());
    await act(async () => button("Two").click());
    await act(async () => button("Send").click());
    assert.deepEqual(frames[0].payload.answer, ["One", "Two"]);
  });
});

const { useDecisionDiscussion } = await import("../components/chat/composer/modes/question/use-decision-discussion.ts");
for (const existingDraft of ["", "Keep my existing question."]) {
  test(`discussion prepares an editable focused draft, preserving ${existingDraft ? "existing text" : "empty input"}`, async () => {
    const declined = [];
    let focusCount = 0;
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    function Harness() {
      const [q, setQ] = useState(decision);
      const [draft, setDraft] = useState(existingDraft);
      const textareaRef = useRef(null);
      const discuss = useDecisionDiscussion({ decision: q, sessionKey: "session-one", input: draft, setInput: setDraft,
        decline: d => declined.push(d), dequeue: () => setQ(null), textareaRef });
      return q ? createElement(QuestionMode, { decision: q, onResolve() {}, onChatAbout: discuss })
        : createElement("textarea", { value: draft, onChange: e => setDraft(e.target.value), ref: node => {
          textareaRef.current = node;
          if (node) node.focus = () => { focusCount++; };
        } });
    }
    try {
      await act(async () => root.render(createElement(Harness)));
      await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "Chat about this").click());
      assert.deepEqual(declined, [decision], "only the displayed wait is declined");
      const textarea = host.querySelector("textarea");
      assert.ok(textarea.value.includes(decision.prompt));
      assert.ok(textarea.value.includes(decision.detail));
      assert.ok(textarea.value.endsWith(existingDraft));
      assert.equal(focusCount, 1, "focus the newly mounted chat input");
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
}

for (const switchSession of [false, true]) {
  test(`discussion focus ${switchSession ? "cancels after switching sessions" : "waits for remaining decisions"}`, async () => {
    let control, focusCount = 0;
    const host = document.createElement("div"); document.body.append(host);
    const root = createRoot(host);
    function Harness() {
      const [q, setQ] = useState(decision);
      const [sid, setSid] = useState("one");
      const [draft, setDraft] = useState("");
      const textareaRef = useRef(null);
      const discuss = useDecisionDiscussion({ decision: q, sessionKey: sid, input: draft, setInput: setDraft,
        decline() {}, dequeue: () => setQ({ ...decision, id: "second" }), textareaRef });
      control = { setQ, setSid };
      return q ? createElement(QuestionMode, { decision: q, onResolve() {}, onChatAbout: discuss })
        : createElement("textarea", { value: draft, readOnly: true, ref: node => {
          textareaRef.current = node; if (node) node.focus = () => { focusCount++; };
        } });
    }
    try {
      await act(async () => root.render(createElement(Harness)));
      await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent === "Chat about this").click());
      assert.equal(focusCount, 0);
      assert.equal(host.querySelector("textarea"), null, "the other pending decision remains visible");
      if (switchSession) await act(async () => control.setSid("two"));
      await act(async () => control.setQ(null));
      assert.equal(focusCount, switchSession ? 0 : 1);
    } finally { await act(async () => root.unmount()); host.remove(); }
  });
}
