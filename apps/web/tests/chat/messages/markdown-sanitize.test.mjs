// Chat markdown is sanitized before it reaches a bubble. marked does not
// sanitize, and content the model relays from tools (fetched pages, repo
// files, MCP results, channel messages) lands here. Rail previews and the
// skill page are covered by untrusted-markdown-surfaces.test.mjs.
import assert from "node:assert/strict";
import { describe, test } from "node:test";
import { installLoader } from "../../_support/load.mjs";
import { installDom } from "../../_support/dom.mjs";

installLoader();
installDom();
const { renderMd, sanitizeHtml } = await import("../../../lib/runtime-bridge/dom/markdown-render.ts");
const { renderMarkdown, renderUserMarkdown } = await import("../../../components/chat/messages/bubbles/markdown.ts");

function dom(html) {
  const host = document.createElement("div");
  host.innerHTML = html;
  return host;
}
const handlers = (host) => [...host.querySelectorAll("*")]
  .flatMap((el) => [...el.attributes].map((attr) => attr.name))
  .filter((name) => name.toLowerCase().startsWith("on"));

describe("renderMd (assistant bubbles)", () => {
  test("renders markdown and reuses the sanitized HTML for the same source", () => {
    const html = renderMd("hello **world**");
    assert.ok(dom(html).querySelector("strong"));
    assert.equal(renderMd("hello **world**"), html);
  });

  test("strips every on* handler", () => {
    const host = dom(renderMd('<img src="/a.png" onerror="x()"> <a href="/b" onclick="x()" onMouseOver="x()">b</a>'));
    assert.ok(host.querySelector("img"));
    assert.deepEqual(handlers(host), []);
  });

  test("drops javascript: and data:text/html URLs but keeps safe ones", () => {
    const host = dom(renderMd([
      "[js](javascript:alert(1))",
      '<a href=" JavaScript:alert(1)">spaced</a>',
      '<a href="data:text/html,<script>alert(1)</script>">html</a>',
      '<img src="data:image/png;base64,AAAA">',
      "[web](https://example.com) [mail](mailto:a@b.c) [rel](/docs) [frag](#x)",
    ].join("\n\n")));
    const hrefs = [...host.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    assert.deepEqual(hrefs, [null, null, null, "https://example.com", "mailto:a@b.c", "/docs", "#x"]);
    assert.equal(host.querySelector("img").getAttribute("src"), "data:image/png;base64,AAAA");
  });

  test("drops embedding, script and form tags with their contents", () => {
    const host = dom(renderMd([
      '<iframe src="https://evil.example"></iframe>',
      '<object data="x.swf"></object><embed src="x.swf">',
      "<script>window.__x = 1</script><style>body{}</style>",
      '<form action="/steal"><input name="q"></form>',
    ].join("\n")));
    assert.equal(host.querySelector("iframe, object, embed, script, style, form, input"), null);
  });

  test("drops SVG and MathML subtrees, including SMIL href mutation", () => {
    for (const input of [
      '<svg><a href="#safe"><text>run</text><animate attributeName="href" values="javascript:window.__smil_xss=1"/></a></svg>',
      '<math><mtext href="javascript:window.__mathml_xss=1">run</mtext></math>',
    ]) {
      const html = renderMd(input);
      assert.equal(dom(html).querySelector("svg, math, animate"), null);
      assert.equal(html.includes("javascript:"), false);
    }
  });

  test("restores math as escaped text, so HTML in a formula never becomes an element", () => {
    // Control: the same markup outside the renderer does become an element.
    assert.ok(dom('<img src="x" onerror="window.__xss=1">').querySelector("img"));
    for (const input of [
      '$$<img src="x" onerror="window.__xss=1">$$',
      '$<img src="x" onerror="window.__xss=1">$',
      '\\[<img src="x" onerror="window.__xss=1">\\]',
      '\\(<img src="x" onerror="window.__xss=1">\\)',
    ]) {
      const host = dom(renderMd(input));
      assert.equal(host.querySelector("img, [onerror]"), null, input);
      assert.equal(host.textContent.trim(), input);
    }
  });

  test("a dollar sign inside a formula is not read as replacement syntax", () => {
    for (const formula of ["$$a $& b $' c $1$$", "$p $` q$"]) {
      assert.equal(dom(renderMd(formula)).textContent.trim(), formula);
    }
  });

  test("sanitizeHtml on its own keeps text and safe markup", () => {
    assert.equal(sanitizeHtml("<p>ok <em>fine</em></p>"), "<p>ok <em>fine</em></p>");
  });
});

describe("renderMarkdown (chat bubble wrapper)", () => {
  test("wraps each table in its .md-table scroll container", () => {
    const host = dom(renderMarkdown("| a | b |\n|---|---|\n| 1 | 2 |"));
    const table = host.querySelector("table");
    assert.ok(table);
    assert.ok(table.parentElement.classList.contains("md-table"));
  });

  test("keeps the sanitizer between marked and the bubble", () => {
    const host = dom(renderMarkdown('hi <img src="x" onerror="x()"> [go](javascript:x())'));
    assert.deepEqual(handlers(host), []);
    assert.equal(host.innerHTML.includes("javascript:"), false);
  });
});

describe("renderUserMarkdown (user and channel-peer bubbles)", () => {
  test("raw HTML stays visible text, URL schemes are filtered, and emphasis still renders", () => {
    const host = dom(renderUserMarkdown('**bold** <img src="x" onerror="window.__user_xss=1"> [go](javascript:window.__user_xss=1)'));
    assert.ok(host.querySelector("strong"));
    assert.equal(host.querySelector("img, [onerror]"), null);
    assert.ok(host.textContent.includes("<img"));
    assert.equal(host.innerHTML.includes("javascript:"), false);
  });
});
