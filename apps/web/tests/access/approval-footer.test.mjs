import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { registerHooks } from "node:module";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { parseHTML } from "linkedom";

const webRoot = new URL("../../", import.meta.url);
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
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { flushSync } = await import("react-dom");
const { QuestionMode } = await import("../../components/chat/composer/modes/question/question-mode.tsx");
globalThis.WebSocket = { OPEN: 1 };
const decision = { id: "wait-one", kind: "approval", prompt: "Allow this command?", detail: "echo test", options: [], allowedScopes: ["once", "always"], multi: false, allow_custom: false, executionId: "exec-one", expectedVersion: 3, waitGeneration: 0 };
const { useDecisionSubmissions } = await import("../../lib/chat/decisions/decision-submissions.ts");
const { useDecisionDrafts } = await import("../../lib/chat/decisions/decision-drafts.ts");
async function mounted(q, check) {
  useDecisionSubmissions.setState({submissions: {}});
  useDecisionDrafts.setState({drafts: {}});
  const frames = [], resolved = [];
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
    await act(async () => root.render(createElement(QuestionMode, { decision: q, onResolve: id => resolved.push(id) })));
    const button = label => [...host.querySelectorAll("button")].find(b => (b.getAttribute("aria-label") ?? b.textContent.replace("✓ ", "").replace("Ctrl ↵", "")) === label);
    await check({ host, button, frames, resolved });
  } finally { await act(async () => root.unmount()); host.remove(); }
}

test("saved approval reports a continuation that could not resume", async () => {
  const notices = [];
  const listener = event => notices.push(event.detail.message);
  window.addEventListener("op:toast", listener);
  try {
    await mounted(decision, async ({ button, resolved }) => {
      respond = async (_url, init) => {
        const command = JSON.parse(init.body);
        return Response.json({ command: { ...command, status: "applied" },
          execution: { execution_id: "exec-one", status: "paused", reason_code: "continuation_contract_mismatch" } });
      };
      await act(async () => button("Allow once").click());
      assert.deepEqual(resolved, [decision.id]);
      assert.equal(notices.length, 1);
      assert.match(notices[0], /answer was saved.*operation has not run/i);
    });
  } finally { window.removeEventListener("op:toast", listener); }
});

for (const [label, action] of [["Allow once", "execution.wait.answer"], ["Deny", "execution.wait.decline"]]) {
  test(`approval ${label} submits one decision directly`, async () => {
    await mounted(decision, async ({ host, button, frames, resolved }) => {
      const footer = host.querySelector('[aria-label="Decision actions"]');
      assert.equal(footer.querySelectorAll("button").length, 4);
      assert.ok(button("Deny with a note"));
      assert.equal(button("Send"), undefined);
      assert.equal(button("Chat about this"), undefined);
      await act(async () => button(label).click());
      assert.equal(frames.length, 1);
      assert.equal(frames[0].action, action);
      assert.deepEqual(resolved, [decision.id]);
    });
  });
}
test("the Worker's tool approval wording is shown in the reader's language", async () => {
  await mounted({ ...decision, prompt: "允许执行 bash？", tool: "bash", args: { command: "echo verified" } }, async ({ host }) => {
    assert.match(host.textContent, /Run this command\?/);
    assert.doesNotMatch(host.textContent, /允许执行/);
  });
  await mounted({ ...decision, prompt: "允许执行 agent？", tool: "agent", args: { description: "Scout" } }, async ({ host }) => {
    assert.match(host.textContent, /Run agent\?/);
  });
  // Any other prompt is the asker's own words and stays as written.
  await mounted({ ...decision, prompt: "Allow this command?", tool: "bash", args: { command: "ls" } }, async ({ host }) => {
    assert.match(host.textContent, /Allow this command\?/);
  });
});

test("a sub-agent's approval names it in the title row; the conversation's own agent's does not", async () => {
  await mounted({ ...decision, askedBy: "PR Verifier", prompt: "Run this command?" }, async ({ host }) => {
    const header = host.querySelector("[data-decision]");
    const who = header.querySelector("[data-asked-by]");
    assert.equal(who.textContent, "PR Verifier");
    assert.equal(who.getAttribute("aria-label"), "Asked by PR Verifier");
    assert.ok(who.querySelector("[data-bot-glyph]"), "its character sits before its name");
    assert.equal(who.nextElementSibling.textContent, "Approval required");
    assert.match(host.textContent, /Run this command\?/);
  });
  // A card that draws its own body (the terminal one) names the asker too.
  await mounted({ ...decision, askedBy: "scout", tool: "terminal_use", args: { terminal_id: "t1", action: "input", data: "ls\r" } }, async ({ host }) => {
    assert.equal(host.querySelector("[data-asked-by]").textContent, "scout");
    assert.match(host.textContent, /Type into your Terminal tab\?/);
  });
  await mounted(decision, async ({ host }) => {
    assert.equal(host.querySelector("[data-asked-by]"), null);
  });
  // A long name is cut inside its badge, so the title and the buttons keep their place.
  const long = "一个名字特别特别特别特别长的子代理 with a very long English tail as well";
  await mounted({ ...decision, askedBy: long }, async ({ host }) => {
    const who = host.querySelector("[data-asked-by]");
    assert.match(who.className, /max-w-\[45%\]/);
    assert.match(who.className, /(^| )shrink( |$)/);
    assert.equal(who.querySelector(".truncate").textContent, long);
    assert.equal(who.getAttribute("aria-label"), `Asked by ${long}`);
  });
});
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
test("multiple questions navigate locally and submit all answers in order", async () => {
  await mounted({ ...decision, kind: "ask_many", questions: [
    { prompt: "First", options: ["One"], multi: false, allow_custom: false },
    { prompt: "Second", options: ["Two"], multi: false, allow_custom: false },
  ] }, async ({ button, frames }) => {
    assert.ok(!button("Two"), "only the current question is visible");
    await act(async () => button("One").click());
    await act(async () => button("Next").click());
    assert.deepEqual(frames, []);
    await act(async () => button("Back").click());
    assert.equal(button("One").getAttribute("aria-pressed"), "true");
    await act(async () => button("Next").click());
    await act(async () => button("Two").click());
    await act(async () => button("Submit").click());
    assert.deepEqual(frames[0].payload.answer, ["One", "Two"]);
  });
});


async function typeNote(host, button, opener, note) {
  await act(async () => flushSync(() => button(opener).click()));
  const input = host.querySelector("textarea");
  const props = input[Object.keys(input).find(key => key.startsWith("__reactProps$"))];
  await act(async () => flushSync(() => props.onChange({ target: { value: note } })));
}

test("deny with a note sends the note as the reason in one decline", async () => {
  await mounted(decision, async ({ host, button, frames, resolved }) => {
    await typeNote(host, button, "Deny with a note", "Keep build/ and just run npm run build");
    assert.equal(button("Allow once"), undefined, "the note replaces the approve controls");
    assert.match(host.textContent, /goes to the agent with the denial/);
    await act(async () => button("Deny and send").click());
    assert.equal(frames.length, 1);
    assert.equal(frames[0].action, "execution.wait.decline");
    assert.equal(frames[0].payload.reason, "Keep build/ and just run npm run build");
    assert.deepEqual(resolved, [decision.id]);
  });
});

test("a question's discussion goes to the agent as the decline, not as a second message", async () => {
  const q = { ...decision, id: "wait-ask", kind: "ask", options: ["A"] };
  await mounted(q, async ({ host, button, frames, resolved }) => {
    await typeNote(host, button, "Discuss", "Why is this needed?");
    await act(async () => button("Send discussion").click());
    assert.equal(frames.length, 1);
    assert.equal(frames[0].action, "execution.wait.decline");
    assert.equal(frames[0].payload.reason, "Why is this needed?");
    assert.deepEqual(resolved, [q.id]);
  });
});

test("an unconfirmed note is retried unchanged", async () => {
  await mounted(decision, async ({ host, button, frames, resolved }) => {
    respond = async (_url, init) => { frames.push(JSON.parse(init.body)); throw new Error("offline"); };
    await typeNote(host, button, "Deny with a note", "Use the staging server");
    await act(async () => button("Deny and send").click());
    assert.deepEqual(resolved, []);
    assert.ok(host.querySelector("textarea").hasAttribute("readonly"), "the sent note is kept as sent");
    respond = async (_url, init) => {
      const command = JSON.parse(init.body); frames.push(command);
      return Response.json({ command: { ...command, status: "applied" } });
    };
    await act(async () => button("Retry").click());
    assert.equal(frames.length, 2);
    assert.equal(frames[0].command_id, frames[1].command_id);
    assert.equal(frames[1].payload.reason, "Use the staging server");
    assert.deepEqual(resolved, [decision.id]);
  });
});

test("a pending note blocks another answer via Ctrl and Meta Enter", async () => {
  await mounted(decision, async ({ host, button, frames }) => {
    let finish;
    respond = async (_url, init) => new Promise(resolve => {
      const command = JSON.parse(init.body); frames.push(command);
      finish = () => resolve(Response.json({ command: { ...command, status: "applied" } }));
    });
    await typeNote(host, button, "Deny with a note", "No");
    await act(async () => button("Deny and send").click());
    const card = host.querySelector("[data-decision]");
    for (const modifier of ["ctrlKey", "metaKey"]) {
      const enter = new Event("keydown", { bubbles: true, cancelable: true });
      Object.defineProperties(enter, { key: { value: "Enter" }, [modifier]: { value: true } });
      await act(async () => card.dispatchEvent(enter));
    }
    const count = frames.length;
    await act(async () => finish());
    assert.equal(count, 1, "no competing answer while the note is pending");
  });
});


test("failed submission leaves the actual answer and error visible in the output", async () => {
  await mounted({ ...decision, id: "wait-inline-error", kind: "ask", options: ["Alice"] }, async ({ host, button, frames }) => {
    respond = async (_url, init) => {
      frames.push(JSON.parse(init.body));
      return Response.json({error: "test_unavailable"}, {status: 503});
    };
    await act(async () => button("Alice").click());
    await act(async () => button("Submit").click());
    const receipt = host.querySelector('[role="status"]');
    assert.ok(receipt, "submission must have visible inline feedback");
    assert.match(host.textContent, /Alice/);
    assert.match(receipt.textContent, /test_unavailable.*503/);
    await act(async () => button("Retry answer").click());
    assert.equal(frames.length, 2);
    assert.equal(frames[0].command_id, frames[1].command_id);
    assert.deepEqual(frames[0].payload, frames[1].payload);
  });
});


test("persistent approval scopes are visible and submit their exact scope", async () => {
  for (const [scope, label] of [["always", "Always allow"], ["always_path", "Always allow this path"]]) {
    await mounted({ ...decision, allowedScopes: ["once", scope] }, async ({ button, frames }) => {
      assert.ok(button(label));
      await act(async () => button(label).click());
      assert.deepEqual(frames.at(-1).payload.answer, { answer: "approve", scope });
    });
  }
  await mounted({ ...decision, allowedScopes: ["once"] }, async ({ button }) => {
    assert.equal(button("Always allow"), undefined);
    assert.equal(button("Always allow this path"), undefined);
  });
});
