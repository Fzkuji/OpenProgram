import assert from "node:assert/strict";
import { createRequire, registerHooks } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parseHTML } = require("linkedom");

// The send queue and i18n stand in: the bridge only needs to hand text on.
const sent = [];
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "@/lib/chat/send/send-queue") {
      return { shortCircuit: true, url: `data:text/javascript,${encodeURIComponent(
        "export function sendFromTranscript(id, text) { globalThis.__sent.push([id, text]); return 'q1'; }")}` };
    }
    if (specifier === "@/lib/i18n") {
      return { shortCircuit: true, url: `data:text/javascript,${encodeURIComponent(
        "export function translateText(en) { return en; }")}` };
    }
    return next(specifier, context);
  },
});
globalThis.__sent = sent;

const { document } = parseHTML("<html><body></body></html>");
const listeners = [];
const opened = [];
const window = {
  addEventListener: (type, fn) => { if (type === "message") listeners.push(fn); },
  matchMedia: () => ({ addEventListener() {} }),
  open: (url) => { opened.push(url); },
};
Object.assign(globalThis, { window, document });
globalThis.MutationObserver = class { observe() {} };
globalThis.getComputedStyle = () => ({
  colorScheme: "light",
  getPropertyValue: (name) => ({ "--accent-fill": "#2563eb", "--text-primary": "#3a3a40", "--bg-primary": "#ffffff" })[name] ?? "",
});

// Each mounted iframe gets a stand-in window that records what it is sent.
const views = [];
const createElement = document.createElement.bind(document);
document.createElement = (name) => {
  const el = createElement(name);
  if (name === "iframe") {
    const view = { posted: [], postMessage(message) { this.posted.push(message); } };
    Object.defineProperty(el, "contentWindow", { value: view });
    views.push(view);
  }
  return el;
};

const { withVisuals, markOpenVisualFence } = await import("../../../lib/chat/markdown/visual.ts");
const { mountVisualsIn, themeCss } = await import("../../../lib/chat/markdown/visual-frames.ts");

const fenced = (body, lang = "visual") => `<pre><code class="language-${lang}">${body}</code></pre>`;
const deliver = (source, data) => listeners.forEach((fn) => fn({ source, data: { opVisual: 1, ...data } }));

test("a finished ```visual block becomes a mount point; an open one a placeholder", () => {
  const html = withVisuals(fenced("&lt;div class=&quot;card&quot;&gt;Hi&lt;/div&gt;") + fenced("x = 1", "python"));
  assert.match(html, /^<div class="op-visual" data-visual="pending"><pre class="op-visual-source" hidden>&lt;div/);
  assert.match(html, /language-python/);
  assert.match(withVisuals(fenced("&lt;div", "visual-partial")), /op-visual-drawing/);
  assert.equal(markOpenVisualFence("Look:\n```visual\n<div>"), "Look:\n```visual-partial\n<div>");
  assert.equal(markOpenVisualFence("```visual\n<div></div>\n```\n"), "```visual\n<div></div>\n```\n");
});

test("a view gets its source and theme, sizes itself, opens links and sends prompts to its conversation", () => {
  const host = document.createElement("div");
  host.innerHTML = `<div class="message assistant" data-session-id="local_s1">${
    withVisuals(fenced("&lt;button onclick=&quot;sendPrompt('Rerun')&quot;&gt;Rerun&lt;/button&gt;"))}</div>`;
  document.body.append(host);
  mountVisualsIn(host);
  const iframe = host.querySelector("iframe.op-visual-frame");
  assert.equal(iframe.getAttribute("sandbox"), "allow-scripts");
  assert.equal(iframe.getAttribute("src"), "/visual-frame");
  const view = views.at(-1);

  deliver(view, { type: "ready" });
  assert.equal(view.posted.length, 1);
  assert.equal(view.posted[0].type, "render");
  assert.equal(view.posted[0].html, `<button onclick="sendPrompt('Rerun')">Rerun</button>`);
  assert.match(view.posted[0].css, /--accent:#2563eb;/);
  deliver(view, { type: "ready" });
  assert.equal(view.posted.length, 1, "rendered once");

  deliver(view, { type: "size", height: 236.4 });
  assert.equal(iframe.style.height, "236px");
  deliver(view, { type: "size", height: 99999 });
  assert.equal(iframe.style.height, "4000px");

  deliver(view, { type: "open-link", url: "https://example.com/a" });
  deliver(view, { type: "open-link", url: "javascript:alert(1)" });
  assert.deepEqual(opened, ["https://example.com/a"]);

  deliver(view, { type: "prompt", text: "  Rerun the failed tasks " });
  assert.deepEqual(sent, [["local_s1", "Rerun the failed tasks"]]);

  // A window this module did not mount is ignored.
  deliver({ postMessage() {} }, { type: "prompt", text: "forged" });
  assert.equal(sent.length, 1);
});

test("the theme block carries only the named tokens", () => {
  assert.equal(themeCss(), ":root{--bg-primary:#ffffff;--text-primary:#3a3a40;--accent:#2563eb;}");
});
