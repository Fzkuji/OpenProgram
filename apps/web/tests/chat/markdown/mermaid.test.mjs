import assert from "node:assert/strict";
import { createRequire, registerHooks } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { parseHTML } = require("linkedom");

// mermaid itself needs real layout; a stand-in accepts sources that start
// with "graph" and draws a placeholder SVG.
const rendered = [];
registerHooks({
  resolve(specifier, context, next) {
    if (specifier === "mermaid") {
      return { shortCircuit: true, url: `data:text/javascript,${encodeURIComponent(`
        export default {
          initialize(config) { globalThis.__mermaidConfig = config; },
          async parse(source) { return source.trim().startsWith("graph"); },
          async render(id, source) { globalThis.__mermaidRendered.push(source); return { svg: "<svg data-id='" + id + "'></svg>" }; },
        };`)}` };
    }
    if (specifier === "@/lib/i18n") {
      return { shortCircuit: true, url: new URL("../../../lib/i18n/index.ts", import.meta.url).href };
    }
    return next(specifier, context);
  },
});
globalThis.__mermaidRendered = rendered;

const { window } = parseHTML("<html><body></body></html>");
Object.assign(globalThis, { window, document: window.document });
globalThis.getComputedStyle = () => ({ color: "", colorScheme: "light", fontFamily: "Inter" });

const { renderMermaidIn } = await import("../../../lib/chat/markdown/mermaid.ts");

const card = (lang, body) => `<div class="md-code"><div class="md-code-head"><span class="md-code-lang">${lang}</span><button type="button" class="md-code-copy"></button></div><pre><code class="language-${lang}">${body}</code></pre></div>`;
const settle = () => new Promise((resolve) => setTimeout(resolve, 10));

test("a mermaid card shows its diagram, and the header switches to the source and back", async () => {
  const host = document.createElement("div");
  host.innerHTML = card("mermaid", "graph TD; A--&gt;B") + card("python", "print(1)");
  document.body.appendChild(host);
  renderMermaidIn(host);
  await settle();
  const [diagramCard, pythonCard] = host.querySelectorAll(".md-code");
  assert.equal(diagramCard.dataset.mermaid, "diagram");
  assert.ok(diagramCard.querySelector(".md-mermaid svg"));
  assert.equal(diagramCard.querySelector("pre").hidden, true);
  assert.deepEqual(rendered, ["graph TD; A-->B"]);
  assert.equal(globalThis.__mermaidConfig.securityLevel, "strict");
  const toggle = diagramCard.querySelector(".md-mermaid-toggle");
  assert.equal(toggle.nextElementSibling.className, "md-code-copy", "the toggle sits before copy");
  assert.equal(pythonCard.dataset.mermaid, undefined, "other code cards are left alone");

  toggle.dispatchEvent(new window.Event("click", { bubbles: true }));
  assert.equal(diagramCard.dataset.mermaid, "code");
  assert.equal(diagramCard.querySelector("pre").hidden, false);
  assert.equal(diagramCard.querySelector(".md-mermaid").hidden, true);
  assert.equal(toggle.getAttribute("aria-pressed"), "true");
  toggle.dispatchEvent(new window.Event("click", { bubbles: true }));
  assert.equal(diagramCard.dataset.mermaid, "diagram");

  renderMermaidIn(host);
  await settle();
  assert.equal(rendered.length, 1, "a rendered card is not drawn twice");
  host.remove();
});

test("a source that does not parse stays code", async () => {
  const host = document.createElement("div");
  host.innerHTML = card("mermaid", "graph TD; A--&gt;B").replace("graph TD; A--&gt;B", "not a diagram");
  document.body.appendChild(host);
  renderMermaidIn(host);
  await settle();
  const only = host.querySelector(".md-code");
  assert.equal(only.dataset.mermaid, "source");
  assert.equal(only.querySelector(".md-mermaid"), null);
  assert.equal(only.querySelector("pre").hidden, false);
  host.remove();
});

test("a card replaced while drawing is not touched", async () => {
  const host = document.createElement("div");
  host.innerHTML = card("mermaid", "graph LR; X--&gt;Y");
  document.body.appendChild(host);
  renderMermaidIn(host);
  const stale = host.querySelector(".md-code");
  host.innerHTML = "";
  await settle();
  assert.equal(stale.querySelector(".md-mermaid"), null);
});
