// A paragraph that cites nothing is marked in the Memory preview: a
// person's own note gets a badge by its block, and a file another tool
// wrote is marked once, by its title, not paragraph by paragraph.
import assert from "node:assert/strict";
import test from "node:test";

import { parseHTML } from "linkedom";
import { marked } from "marked";

import { installLoader } from "../_support/load.mjs";
import { renderObsidianMarkdown } from "../../components/memory/markdown/obsidian-markdown.ts";

installLoader();
const { markOrigins } = await import("../../components/memory/markdown/origin-marks.ts");

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
const { sanitizeHtml } = await import("../../lib/runtime-bridge/dom/markdown-render.ts");

const SOURCE = [
  "# Tea",
  "",
  "I prefer green tea. ^aaaa1111",
  "",
  "Cited fact.[^e-1] ^bbbb2222",
  "",
  "[^e-1]: Time: `2026-01-01`; Sources: [said](/s/s1#m1)",
].join("\n");

const html = () => sanitizeHtml(renderObsidianMarkdown(
  SOURCE, (part) => marked.parse(part, { breaks: true, async: false }),
));

const ORIGINS = {
  origin: "runtime",
  paragraphs: [
    { memory_id: "aaaa1111", line: 3, derived: false, origin: "person" },
    { memory_id: "bbbb2222", line: 5, derived: false, origin: "cited" },
  ],
};

test("a person's paragraph gets a badge beside it, a cited one does not", () => {
  const host = document.createElement("main");
  host.innerHTML = markOrigins(html(), ORIGINS, "Added by you");
  const badges = [...host.querySelectorAll('[data-slot="badge"]')];
  assert.equal(badges.length, 1);
  assert.equal(badges[0].textContent, "Added by you");
  assert.equal(badges[0].getAttribute("data-origin"), "person");
  assert.match(badges[0].closest("p").textContent, /I prefer green tea\./);
  // The words are the whole explanation: no hover text on a non-control.
  assert.equal(badges[0].getAttribute("title"), null);
});

test("a file written outside is not marked paragraph by paragraph", () => {
  const outside = { origin: "outside", paragraphs: ORIGINS.paragraphs.map((row) => ({ ...row, origin: "outside" })) };
  assert.equal(markOrigins(html(), outside, "Added by you"), html());
  assert.equal(markOrigins(html(), null, "Added by you"), html());
});

test("a label is text, never markup", () => {
  const host = document.createElement("main");
  host.innerHTML = markOrigins(html(), ORIGINS, "<b>you</b>");
  assert.equal(host.querySelector("b"), null);
  assert.equal(host.querySelector('[data-slot="badge"]').textContent, "<b>you</b>");
});
