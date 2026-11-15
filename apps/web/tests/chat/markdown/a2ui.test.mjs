import assert from "node:assert/strict";
import test from "node:test";

import { markOpenA2uiFence, renderA2ui } from "../../../lib/chat/markdown/a2ui.ts";

const text = (md, inline) => (inline ? `[i:${md}]` : `[b:${md}]`);
const surface = (components) => JSON.stringify({ components });

test("A stats card renders as nested layout with headings and captions", () => {
  const html = renderA2ui(surface([
    { id: "root", component: "Card", child: "body" },
    { id: "body", component: "Column", children: ["title", "stats"] },
    { id: "title", component: "Text", text: "## Run", variant: "h3" },
    { id: "stats", component: "Row", children: ["a"] },
    { id: "a", component: "Column", weight: 1, children: ["v", "l"] },
    { id: "v", component: "Text", text: "500", variant: "h2" },
    { id: "l", component: "Text", text: "tasks", variant: "caption" },
  ]), text);
  assert.ok(html.startsWith('<div class="a2ui"><div class="a2ui-card">'));
  assert.match(html, /role="heading" aria-level="3">\[i:Run\]/);
  assert.match(html, /class="a2ui-col" style="flex:1 1 0"/);
  assert.match(html, /data-variant="caption">\[b:tasks\]/);
});

test("Envelope messages and JSONL carry components and the data model", () => {
  const envelope = [
    JSON.stringify({ version: "v0.9.1", createSurface: { surfaceId: "s", catalogId: "basic" } }),
    JSON.stringify({ version: "v0.9.1", updateComponents: { surfaceId: "s", components: [
      { id: "root", component: "List", children: { componentId: "item", path: "/items" } },
      { id: "item", component: "Text", text: { path: "name" } },
    ] } }),
    JSON.stringify({ version: "v0.9.1", updateDataModel: { surfaceId: "s", path: "/items", value: [{ name: "a" }, { name: "b" }] } }),
  ].join("\n");
  const html = renderA2ui(envelope, text);
  assert.match(html, /\[b:a\][\s\S]*\[b:b\]/);
});

test("Anything that is not a surface keeps the code block", () => {
  assert.equal(renderA2ui('{"components": [', text), null);
  assert.equal(renderA2ui(surface([{ id: "x", component: "Text", text: "no root" }]), text), null);
  assert.equal(renderA2ui(surface([{ id: "root", component: "Button", child: "t" }]), text), null);
  assert.equal(renderA2ui("plain words", text), null);
});

test("Cycles and unknown children render nothing instead of looping", () => {
  const html = renderA2ui(surface([
    { id: "root", component: "Column", children: ["a", "missing"] },
    { id: "a", component: "Card", child: "root" },
  ]), text);
  assert.equal(html, '<div class="a2ui"><div class="a2ui-col"><div class="a2ui-card"></div></div></div>');
});

test("Model-supplied values cannot inject markup or unsafe URLs", () => {
  const html = renderA2ui(surface([
    { id: "root", component: "Column", justify: "x\" onclick=\"y", children: ["img", "bad", "tabs", "icon", "path"] },
    { id: "img", component: "Image", url: "https://e.test/a.png\" onerror=\"x", description: "<b>" },
    { id: "bad", component: "Image", url: "javascript:alert(1)" },
    { id: "tabs", component: "Tabs", tabs: [{ title: "<script>", child: "img" }] },
    { id: "icon", component: "Icon", name: "constructor" },
    { id: "path", component: "Icon", name: { svgPath: "M0 0\"/><script>" } },
  ]), text);
  assert.doesNotMatch(html, /onclick|onerror="|<script|javascript:|<b>/);
  assert.match(html, /src="https:\/\/e\.test\/a\.png&quot; onerror=&quot;x"/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /data-justify/);
});

test("A read-only checkbox and named icons render from fixed tables", () => {
  const html = renderA2ui(surface([
    { id: "root", component: "Row", children: ["done", "todo", "warn"] },
    { id: "done", component: "CheckBox", label: "Download", value: true },
    { id: "todo", component: "CheckBox", label: "Train", value: false },
    { id: "warn", component: "Icon", name: "warning" },
  ]), text);
  assert.match(html, /data-checked="true"[\s\S]*<svg class="a2ui-check"/);
  assert.match(html, /data-checked="false"><span class="a2ui-box"><\/span>/);
  assert.match(html, /data-icon="warning"/);
});

test("Only a fence still open at the end of the text is marked partial", () => {
  assert.equal(markOpenA2uiFence("Hi\n```a2ui\n{\"comp"), "Hi\n```a2ui-partial\n{\"comp");
  const closed = "```a2ui\n{}\n```\nafter";
  assert.equal(markOpenA2uiFence(closed), closed);
  const other = "```js\nconst a2ui = 1";
  assert.equal(markOpenA2uiFence(other), other);
  // A shorter fence inside does not close a longer one.
  assert.equal(markOpenA2uiFence("````a2ui\n```\n"), "````a2ui-partial\n```\n");
  assert.equal(markOpenA2uiFence("~~~a2ui\n{}\n~~~"), "~~~a2ui\n{}\n~~~");
});
