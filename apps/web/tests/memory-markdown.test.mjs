import assert from "node:assert/strict";
import test from "node:test";

import { parseHTML } from "linkedom";
import { marked } from "marked";

import { renderObsidianMarkdown } from "../components/memory/obsidian-markdown.ts";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
const { sanitizeHtml } = await import("../lib/runtime-bridge/markdown-render.ts");

function render(source) {
  const html = sanitizeHtml(renderObsidianMarkdown(
    source,
    (part) => marked.parse(part, { breaks: true, async: false }),
  ));
  const host = document.createElement("main");
  host.innerHTML = html;
  return host;
}

test("Memory preview renders evidence as numbered footnotes and hides block IDs", () => {
  const host = render([
    "# Agent Memory 研究调研",
    "",
    "First fact.[^e-first] ^block-one",
    "",
    "Second fact.[^e-first][^e-second] ^block-two ^legacy-block",
    "",
    "Inline evidence.^[This is an inline footnote.]",
    "",
    "[^e-first]: Time: `2026-08-17`; Sources: [first source](../sources/first.md#source-1)",
    "[^e-second]: Time: `2026-08-18`; Sources: [second source](../sources/second.md#source-2)",
    "  Continued with two leading spaces.",
  ].join("\n"));

  assert.deepEqual(
    [...host.querySelectorAll("[data-footnote-ref]")].map((node) => node.textContent),
    ["[1]", "[1]", "[2]", "[3]"],
  );
  assert.equal(host.querySelectorAll("[data-footnotes] li").length, 3);
  assert.equal(host.querySelectorAll("[data-footnote-backref]").length, 4);
  assert.equal(host.querySelector("[data-footnotes] > hr") !== null, true);
  assert.equal(host.lastElementChild?.matches("[data-footnotes]"), true);
  assert.ok(host.querySelector('[id="^block-one"]'));
  assert.ok(host.querySelector('[id="^block-two"]'));
  assert.ok(host.querySelector('[id="^legacy-block"]'));
  assert.doesNotMatch(host.textContent, /\[\^e-|\^block-|\^legacy-block/);
  assert.match(host.querySelector("[data-footnotes]")?.textContent ?? "", /Time: 2026-08-17; Sources: first source/);
  assert.match(host.querySelector("[data-footnotes]")?.textContent ?? "", /Continued with two leading spaces/);
  assert.deepEqual(
    [...host.querySelectorAll("[data-footnote-backref]")].map((node) => node.textContent),
    ["↩︎", "↩︎", "↩︎", "↩︎"],
  );
  assert.equal(host.querySelector("[data-footnotes] a[href='../sources/first.md#source-1']")?.textContent, "first source");
});

test("Memory preview leaves code and unresolved references literal", () => {
  const host = render([
    "Known.[^known] ^known-block",
    "",
    "`literal [^known] ^literal-block` and unresolved[^missing] with invalid block ^not_valid",
    "",
    "```markdown",
    "Code [^known] ^code-block",
    "[^known]: not a definition inside code",
    "```",
    "",
    "> ~~~markdown",
    "> Quoted code [^known] ^quoted-code-block",
    "> ~~~",
    "",
    "- ~~~markdown",
    "  List code [^known] ^list-code-block",
    "  ~~~",
    "",
    "10. ~~~markdown",
    "    Ordered list code [^known] ^ordered-list-code-block",
    "    ~~~",
    "",
    "[^known]: Footnote with <img src=x onerror=alert(1)>",
  ].join("\n"));

  assert.equal(host.querySelectorAll("[data-footnote-ref]").length, 1);
  assert.equal(host.querySelectorAll("[data-footnote-backref]").length, 1);
  assert.match(host.textContent, /literal \[\^known\] \^literal-block/);
  assert.match(host.textContent, /Code \[\^known\] \^code-block/);
  assert.match(host.textContent, /Quoted code \[\^known\] \^quoted-code-block/);
  assert.match(host.textContent, /List code \[\^known\] \^list-code-block/);
  assert.match(host.textContent, /Ordered list code \[\^known\] \^ordered-list-code-block/);
  assert.match(host.textContent, /unresolved\[\^missing\]/);
  assert.match(host.textContent, /\^not_valid/);
  assert.equal(host.querySelector('[id="^not_valid"]'), null);
  const image = host.querySelector("img");
  assert.equal(image?.hasAttribute("onerror"), false);
  assert.equal(image?.hasAttribute("src"), false);
  assert.equal(host.querySelector("[onerror]") === null, true);
});

test("Memory preview accepts independent fence indentation", () => {
  const host = render([
    "  ~~~markdown",
    "Literal [^known] ^code-block",
    "~~~",
    "",
    "Known.[^known] ^known-block",
    "",
    "[^known]: Definition",
  ].join("\n"));

  assert.equal(host.querySelectorAll("[data-footnote-ref]").length, 1);
  assert.equal(host.querySelectorAll("[data-footnote-backref]").length, 1);
  assert.match(host.textContent, /Literal \[\^known\] \^code-block/);
  assert.doesNotMatch(host.textContent, /\^known-block/);
});
