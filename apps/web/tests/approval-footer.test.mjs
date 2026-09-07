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
const { flushSync } = await import("react-dom");
const { QuestionMode } = await import("../components/chat/composer/modes/question/question-mode.tsx");
globalThis.WebSocket = { OPEN: 1 };
const decision = { id: "wait-one", kind: "approval", prompt: "Allow this command?", detail: "echo test", options: [], allowedScopes: ["once", "always"], multi: false, allow_custom: false, executionId: "exec-one", expectedVersion: 3, waitGeneration: 0 };
async function mounted(q, check) {
  const frames = [], resolved = [], discussed = [];
  globalThis.approvalSocket = { readyState: 1, send: value => frames.push(JSON.parse(value)) };
  respond = async (_url, init) => {
    const command = JSON.parse(init.body);
    frames.push(command);
    return Response.json({ command: { ...command, status: "applied" } });
  };
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(QuestionMode, { decision: q, onResolve: id => resolved.push(id), onChatAbout: () => discussed.push(q.id) })));
    const button = label => [...host.querySelectorAll("button")].find(b => b.textContent.replace("✓ ", "") === label);
    await check({ host, button, frames, resolved, discussed });
  } finally { await act(async () => root.unmount()); host.remove(); }
}

for (const [label, action] of [["Allow once", "execution.wait.answer"], ["Deny", "execution.wait.decline"]]) {
  test(`approval ${label} submits one decision directly`, async () => {
    await mounted(decision, async ({ host, button, frames, resolved }) => {
      const footer = host.querySelector('[aria-label="Decision actions"]');
      assert.equal(footer.querySelectorAll("button").length, 2);
      assert.equal(button("Send"), undefined);
      assert.equal(button("Chat about this"), undefined);
      await act(async () => button(label).click());
      assert.equal(frames.length, 1);
      assert.equal(frames[0].action, action);
      assert.deepEqual(resolved, [decision.id]);
    });
  });
}
test("approval remains pending until its answer is acknowledged", async () => {
  await mounted(decision, async ({ button, resolved }) => {
    let acknowledge;
    respond = async (_url, init) => new Promise(resolve => {
      const command = JSON.parse(init.body);
      acknowledge = () => resolve(Response.json({ command: { ...command, status: "applied" } }));
    });
    await act(async () => button("Allow once").click());
    assert.deepEqual(resolved, []);
    assert.equal(button("Sending…").disabled, true);
    assert.equal(button("Deny").disabled, true);
    await act(async () => acknowledge());
    assert.deepEqual(resolved, [decision.id]);
  });
});
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
const { useSendQueue, registerChatSender } = await import("../lib/state/send-queue.ts");
const { useSessionStore } = await import("../lib/session-store/index.ts");

async function discussionMounted(check) {
  const requests = [], sent = [], removed = [], notices = [], frames = [];
  globalThis.approvalSocket = { readyState: 1, send: value => frames.push(JSON.parse(value)) };
  const onToast = e => notices.push(e.detail); window.addEventListener("op:toast", onToast);
  const q = { ...decision, kind: "ask", sessionId: "origin", tool: "process" };
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  const draft = "My unsent question";
  let changeSession;
  useSessionStore.setState({ runningTasks: {}, currentSessionId: "origin", activeChatKey: "origin" });
  useSendQueue.setState({ queues: {} });
  registerChatSender(args => { sent.push(args); return true; });
  respond = async (url, init) => {
    const command = JSON.parse(init.body); requests.push({ url, command });
    return Response.json({ command: { ...command, status: "applied" } });
  };
  function Harness() {
    const [active, setActive] = useState(q);
    const [sid, setSid] = useState("origin");
    const [input, setInput] = useState(draft);
    changeSession = setSid;
    const textareaRef = useRef(null);
    const discuss = useDecisionDiscussion({ decision: active, sessionKey: sid, thinking: "medium",
      input, setInput, decline() {}, textareaRef,
      dequeue: id => { removed.push(id); setActive(null); } });
    return active ? createElement(QuestionMode, { decision: active, onResolve() {}, onChatAbout: discuss })
      : createElement("textarea", { value: input, readOnly: true, ref: textareaRef });
  }
  try {
    await act(async () => root.render(createElement(Harness)));
    const click = async () => {
      const open = [...host.querySelectorAll("button")].find(b => b.textContent === "Chat about this");
      if (open) {
        await act(async () => flushSync(() => open.click()));
        const input = host.querySelector("textarea");
        const props = input[Object.keys(input).find(key => key.startsWith("__reactProps$"))];
        await act(async () => flushSync(() => props.onChange({ target: { value: "Why is this command needed?" } })));
      }
      const send = [...host.querySelectorAll("button")].find(b => ["Send discussion", "Retry discussion"].includes(b.textContent));
      send?.click();
    };
    await check({ host, requests, sent, removed, notices, frames, click, q, changeSession, draft });
  } finally { await act(async () => root.unmount()); host.remove(); window.removeEventListener("op:toast", onToast); useSendQueue.setState({ queues: {} }); }
}

test("Sending feedback acknowledges decline and sends contextual discussion without changing draft", async () => {
  await discussionMounted(async ({ host, click, requests, sent, draft }) => {
    await act(async () => { await click(); await click(); });
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/execution/wait/decline");
    assert.equal(requests[0].command.payload.wait_id, decision.id);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].sessionId, "origin");
    assert.match(sent[0].text, /Why is this command needed\?[\s\S]*Allow this command\?[\s\S]*echo test/);
    assert.ok(!sent[0].text.includes('{'));
    assert.equal(sent[0].toolsEnabled, false);
    assert.equal(sent[0].webSearchEnabled, false);
    assert.equal(host.querySelector("textarea").value, draft);
  });
});

test("discussion waits for applied acknowledgement and remains owned by the original session", async () => {
  await discussionMounted(async ({ click, sent, changeSession }) => {
    let finish;
    respond = async (_url, init) => new Promise(resolve => { finish = () => resolve(Response.json({ command: { ...JSON.parse(init.body), status: "applied" } })); });
    await act(async () => click());
    assert.equal(sent.length, 0);
    assert.equal(document.querySelector('[aria-busy="true"]').disabled, true);
    await act(async () => changeSession("other"));
    await act(async () => finish());
    assert.equal(sent.length, 1);
    assert.equal(sent[0].sessionId, "origin");
  });
});

for (const status of ["rejected", "accepted", "wrong-id", "network-error"]) {
  test(`discussion does not send on ${status} acknowledgement`, async () => {
    await discussionMounted(async ({ click, sent, removed, notices }) => {
      respond = async (_url, init) => {
        if (status === "network-error") throw new Error("offline");
        const command = JSON.parse(init.body);
        return Response.json({ command: { ...command, command_id: status === "wrong-id" ? "different" : command.command_id, status: status === "wrong-id" ? "applied" : status } });
      };
      await act(async () => click());
      assert.equal(sent.length, 0);
      assert.equal(removed.length, 0);
      assert.equal(notices.at(-1).tone, "error");
      assert.match(notices.at(-1).message, /not sent/);
    });
  });
}

test("discussion queues until the rejected execution clears and retries a disconnected sender", async () => {
  await discussionMounted(async ({ click, sent }) => {
    useSessionStore.setState({ runningTasks: { origin: { execution_id: "exec-one" } } });
    await act(async () => click());
    assert.equal(sent.length, 0);
    assert.equal(useSendQueue.getState().queues.origin.length, 1);
    useSessionStore.setState({ runningTasks: {} });
    registerChatSender(() => false);
    useSendQueue.getState().drain("origin");
    assert.equal(useSendQueue.getState().queues.origin.length, 1);
    registerChatSender(args => { sent.push(args); return true; });
    useSendQueue.getState().drain("origin");
    assert.equal(sent.length, 1);
    assert.equal(useSendQueue.getState().queues.origin?.length ?? 0, 0);
  });
});

test("pending discussion blocks another answer via Ctrl and Meta Enter", async () => {
  await discussionMounted(async ({ host, click, frames }) => {
    let finish;
    respond = async (_url, init) => new Promise(resolve => { finish = () => resolve(Response.json({ command: { ...JSON.parse(init.body), status: "applied" } })); });
    const allow = host.querySelector("[data-decision]");
    await act(async () => click());
    for (const modifier of ["ctrlKey", "metaKey"]) {
      const enter = new Event("keydown", { bubbles: true, cancelable: true });
      Object.defineProperties(enter, { key: { value: "Enter" }, [modifier]: { value: true } });
      await act(async () => allow.dispatchEvent(enter));
    }
    const count = frames.length;
    await act(async () => finish());
    assert.equal(count, 0, "no competing approval while rejection is pending");
  });
});
