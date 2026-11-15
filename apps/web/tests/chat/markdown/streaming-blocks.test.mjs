import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;

const { splitMarkdownBlocks } = await import("../../../lib/chat/markdown/streaming-blocks.ts");
const { renderMd } = await import("../../../lib/runtime-bridge/dom/markdown-render.ts");

const inner = (html) => html.replace(/^<span class="md-rendered">/, "").replace(/<\/span>$/, "");

test("chunks rejoin to the source and split only between top-level blocks", () => {
  const src = "# Title\n\nFirst paragraph\nstill first.\n\n- a\n- b\n\n- c\n\n```py\nx = 1\n\ny = 2\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nLast";
  const chunks = splitMarkdownBlocks(src);
  assert.equal(chunks.join(""), src);
  assert.deepEqual(chunks, [
    "# Title\n\n",
    "First paragraph\nstill first.\n\n",
    "- a\n- b\n\n- c\n\n",
    "```py\nx = 1\n\ny = 2\n```\n\n",
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n",
    "Last",
  ]);
});

test("finished chunks render as the whole text does", () => {
  for (const src of [
    "Intro with **bold** and `code`.\n\n1. one\n2. two\n\n   indented continuation\n\n> quote\n\n---\n\nEnd $a_b$ here.",
    "A\n\n$$\nx = 1\n\ny = 2\n$$\n\nB \\(c\\)",
    "Text\n\n~~~\nfence\n\n~~~\n\n    indented code\n\nTail",
    "- a\nlazy\n\n- b\n\nPara\n\n2. two\n3. three",
  ]) {
    const chunks = splitMarkdownBlocks(src);
    assert.ok(chunks.length > 1, src);
    assert.equal(chunks.map((chunk) => inner(renderMd(chunk))).join(""), inner(renderMd(src)), src);
  }
});

test("open fences, open formulas and joined sources do not split", () => {
  assert.deepEqual(splitMarkdownBlocks("Before\n\n```js\nconst a = 1;\n\nconst b"), ["Before\n\n", "```js\nconst a = 1;\n\nconst b"]);
  assert.deepEqual(splitMarkdownBlocks("Before\n\n$$\nx\n\ny"), ["Before\n\n", "$$\nx\n\ny"]);
  for (const src of ["See [docs][1].\n\nMore\n\n[1]: https://example.com", "Note[^1]\n\nMore", "<details>\n\n**x**\n\n</details>"]) {
    assert.deepEqual(splitMarkdownBlocks(src), [src]);
  }
  assert.deepEqual(splitMarkdownBlocks(""), [""]);
});
