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
    if (specifier.endsWith(".module.css")) {
      return { url: "data:text/javascript,export default {}", shortCircuit: true };
    }
    const base = specifier.startsWith("@/")
      ? new URL(specifier.slice(2), webRoot).href
      : specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)
        ? new URL(specifier, context.parentURL).href
        : null;
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
        format: "module",
        shortCircuit: true,
        source: ts.transpileModule(readFileSync(fileURLToPath(url), "utf8"), {
          compilerOptions: {
            jsx: ts.JsxEmit.ReactJSX,
            module: ts.ModuleKind.ESNext,
            target: ts.ScriptTarget.ES2022,
          },
        }).outputText,
      };
    }
    return nextLoad(url, context);
  },
});

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
Object.defineProperty(document, "compatMode", { value: "CSS1Compat" });
if (typeof globalThis.Node === "undefined") globalThis.Node = window.Node;
globalThis.NodeFilter = window.NodeFilter || {
  SHOW_TEXT: 4,
  FILTER_REJECT: 2,
  FILTER_ACCEPT: 1,
  FILTER_SKIP: 3,
};
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
window.ResizeObserver = globalThis.ResizeObserver;
window.NodeFilter = globalThis.NodeFilter;
document.createTreeWalker = () => ({ nextNode() { return null; } });
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
globalThis.cancelAnimationFrame = clearTimeout;
globalThis.Event = window.Event;
globalThis.CustomEvent = window.CustomEvent;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.localStorage = {
  getItem(key) {
    return key === "agentic_locale" ? "en" : null;
  },
  setItem() {},
  removeItem() {},
};
window.matchMedia = () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
});
window.location = { pathname: "/chat", hash: "", search: "" };
window.history = { replaceState() {}, pushState() {} };

const SAMPLE =
  "The relative drop is \\(d_t\\). \\(v_t = (d_t - d_{t+1}) / d_t\\).";

const { typesetMath } = await import(
  "../lib/runtime-bridge/markdown-render.ts"
);
const { renderMarkdown } = await import(
  "../components/chat/messages/markdown.ts"
);

function paint(src) {
  const host = document.createElement("div");
  host.className = "chat-text message-content";
  host.innerHTML = renderMarkdown(src);
  typesetMath(host);
  return host;
}

test("chat renderMarkdown preserves sample delimiters until typeset", () => {
  const html = renderMarkdown(SAMPLE);
  assert.match(html, /\\\(d_t\\\)/);
  assert.equal(html.includes("<span class=\"katex\""), false);
});

test("user sample typesets on the chat bubble path", () => {
  const host = paint(SAMPLE);
  assert.equal(host.querySelector(".katex") !== null, true);
  assert.equal(host.textContent.includes("\\(d_t\\)"), false);
});

test("display math \\[ \\] and $$ typeset", () => {
  assert.equal(paint("See \\[a^2 + b^2\\] end").querySelector(".katex") !== null, true);
  assert.equal(paint("See $$x_{t+1}$$ end").querySelector(".katex") !== null, true);
});

test("inline $...$ typesets", () => {
  assert.equal(paint("Let $a_b$ hold").querySelector(".katex") !== null, true);
});

test("prose and code without closed math stay literal", () => {
  const prose = paint("no formulas here");
  assert.equal(prose.querySelector(".katex") !== null, false);
  const inline = paint("use `plain code` here");
  assert.equal(inline.querySelector(".katex") !== null, false);
  assert.match(inline.textContent, /plain code/);
});

test("incomplete streaming math does not typeset until closed", () => {
  const open = paint("start \\(d_t");
  assert.equal(open.querySelector(".katex") !== null, false);
  const closed = paint("start \\(d_t\\) end");
  assert.equal(closed.querySelector(".katex") !== null, true);
});

test("invalid math does not throw", () => {
  assert.doesNotThrow(() => paint("\\( { \\)"));
});

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { AssistantBubble } = await import(
  "../components/chat/messages/assistant-bubble.tsx"
);

test("assistant bubble mount, stream update, and history paint sample math", async () => {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const msg = (content, status) => ({
    id: "m1",
    role: "assistant",
    content,
    status,
    tools: [],
  });
  try {
    await act(async () => {
      root.render(createElement(AssistantBubble, { msg: msg("start \\(d_t", "streaming") }));
    });
    assert.equal(host.querySelector(".katex") !== null, false);

    await act(async () => {
      root.render(createElement(AssistantBubble, { msg: msg(SAMPLE, "streaming") }));
    });
    assert.equal(host.querySelector(".katex") !== null, true);

    await act(async () => {
      root.render(createElement(AssistantBubble, { msg: msg(SAMPLE, "done") }));
    });
    assert.equal(host.querySelector(".katex") !== null, true);
    assert.equal(host.textContent.includes("\\(d_t\\)"), false);
  } finally {
    await act(async () => {
      root.unmount();
    });
    host.remove();
  }
});
