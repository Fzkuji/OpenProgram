import assert from "node:assert/strict";
import test from "node:test";

import { parseHTML } from "linkedom";
import { marked } from "marked";

import { renderObsidianMarkdown } from "../../components/memory/markdown/obsidian-markdown.ts";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
const { sanitizeHtml } = await import("../../lib/runtime-bridge/dom/markdown-render.ts");

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
    "    Continued with four leading spaces.",
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
  assert.match(host.querySelector("[data-footnotes]")?.textContent ?? "", /Continued with four leading spaces/);
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

test("Memory preview preserves indented code, escaped references, and source tokens", () => {
  const host = render([
    "Known.[^known] ^known-block",
    "",
    "    Code [^known] ^code-block",
    "",
    String.raw`Escaped \[^known] and escaped inline \^[literal note].`,
    "",
    "Literal private token: \uE0000\uE001",
    "",
    "[^known]: Definition",
  ].join("\n"));

  assert.equal(host.querySelectorAll("[data-footnote-ref]").length, 1);
  assert.equal(host.querySelectorAll("[data-footnote-backref]").length, 1);
  assert.match(host.textContent, /Code \[\^known\] \^code-block/);
  assert.match(host.textContent, /Escaped \[\^known\] and escaped inline \^\[literal note\]/);
  assert.match(host.textContent, /Literal private token: \uE0000\uE001/);
});

test("Memory preview distinguishes container code from list continuation", () => {
  const host = render([
    "Known.[^known] ^known-block",
    "",
    ">     Quoted code [^known] ^quoted-code-block",
    "",
    "10. item",
    "    continuation [^known] ^para-block",
    "",
    "[^known]: Definition",
  ].join("\n"));

  assert.equal(host.querySelectorAll("[data-footnote-ref]").length, 2);
  assert.equal(host.querySelectorAll("[data-footnote-backref]").length, 2);
  assert.match(host.textContent, /Quoted code \[\^known\] \^quoted-code-block/);
  assert.doesNotMatch(host.textContent, /\^para-block/);
  assert.ok(host.querySelector('[id="^para-block"]'));
});

const { groupTimelineDays } = await import("../../components/memory/markdown/format.ts");

test("the timeline groups days by year then month, newest first", () => {
  const grouped = groupTimelineDays([
    { date: "2026-08-07", size: 20, mtime: 2 },
    { date: "2025-12-31", size: 10, mtime: 1 },
    { date: "2026-08-15", size: 30, mtime: 3 },
  ], "en");
  assert.deepEqual(
    grouped.map((year) => [year.year, year.entryCount, year.months.map((month) => [month.label, month.days.map((day) => day.dayLabel)])]),
    [["2026", 2, [["August", ["15", "07"]]]], ["2025", 1, [["December", ["31"]]]]],
  );
});

test("the timeline drops impossible dates", () => {
  assert.deepEqual(groupTimelineDays([
    { date: "2026-13-01", size: 1, mtime: 1 },
    { date: "2026-02-31", size: 1, mtime: 1 },
    { date: "not-a-date", size: 1, mtime: 1 },
  ]), []);
});

test("year and month overview files sit on their year and month, and early years keep four digits", () => {
  const grouped = groupTimelineDays([
    { date: "2026", size: 1, mtime: 1 },
    { date: "2026-08", size: 1, mtime: 1 },
    { date: "0099-01-01", size: 1, mtime: 1 },
  ], "en");
  assert.deepEqual(
    grouped.map((year) => [year.year, year.entryCount, year.entries.map((entry) => entry.label), year.months.map((month) => [month.label, month.entries.map((entry) => entry.label), month.days.map((day) => day.dayLabel)])]),
    [["2026", 2, ["Year overview"], [["August", ["Month overview"], []]]], ["0099", 1, [], [["January", [], ["01"]]]]],
  );
});
