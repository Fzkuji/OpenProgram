import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<!doctype html><html><body></body></html>");
globalThis.window = window;
globalThis.document = window.document;
// KaTeX refuses to draw in quirks mode.
Object.defineProperty(document, "compatMode", { value: "CSS1Compat" });
const { renderMd, typesetMath } = await import("../../../lib/runtime-bridge/dom/markdown-render.ts");

const body = (src) => renderMd(src, { cache: false }).replace(/^<span class="md-rendered">|<\/span>$/g, "").trim();
const inline = (src) => body(src).replace(/^<p>|<\/p>$/g, "");

test("emphasis next to Chinese punctuation", () => {
  assert.equal(inline("这是*斜体。*后面"), "这是<em>斜体。</em>后面");
  assert.equal(inline("***粗斜。***后面"), "<strong><em>粗斜。</em></strong>后面");
  assert.equal(inline("价格~~原价，~~现价"), "价格<del>原价，</del>现价");
  assert.equal(inline("这是__粗体，__文字"), "这是<strong>粗体，</strong>文字");
  assert.equal(inline("这是_斜体_文字"), "这是<em>斜体</em>文字");
  assert.equal(inline("**粗体里*斜体*。**后面"), "<strong>粗体里<em>斜体</em>。</strong>后面");
});

test("identifiers and Latin emphasis are left to the stock rules", () => {
  assert.equal(inline("调用 get_user_name 函数"), "调用 get_user_name 函数");
  assert.equal(inline("文件 report_v1_final.md"), "文件 report_v1_final.md");
  assert.equal(inline("*em* **strong** ~~del~~ a*b*c"), "<em>em</em> <strong>strong</strong> <del>del</del> a<em>b</em>c");
  assert.equal(inline("2 * 3 * 4"), "2 * 3 * 4");
});

test("a bare address ends at Chinese punctuation", () => {
  assert.equal(inline("访问 https://example.com/a。下一句"), '访问 <a href="https://example.com/a">https://example.com/a</a>。下一句');
  assert.equal(inline("见https://example.com/a，然后"), '见<a href="https://example.com/a">https://example.com/a</a>，然后');
  assert.equal(inline("（参考 https://example.com/a）后面"), '（参考 <a href="https://example.com/a">https://example.com/a</a>）后面');
  assert.equal(inline("“https://a.com/x”很好"), '“<a href="https://a.com/x">https://a.com/x</a>”很好');
  assert.equal(inline("见 www.example.com。好"), '见 <a href="http://www.example.com">www.example.com</a>。好');
  assert.equal(inline("打开https://a.com下一句"), '打开<a href="https://a.com">https://a.com</a>下一句');
  assert.match(inline("看 https://zh.wikipedia.org/wiki/中文 页面"), />https:\/\/zh\.wikipedia\.org\/wiki\/中文<\/a> 页面$/);
  assert.equal(inline("see https://example.com/a. Next"), 'see <a href="https://example.com/a">https://example.com/a</a>. Next');
});

test("a link written with full-width parentheses", () => {
  assert.equal(inline("[文档]（https://a.com/x）后面"), '<a href="https://a.com/x">文档</a>后面');
  assert.equal(inline("[文档]（随便写的）"), "[文档]（随便写的）");
});

test("headings and lists without the space after the marker", () => {
  assert.equal(body("##标题\n正文"), "<h2>标题</h2>\n<p>正文</p>");
  assert.equal(body("正文\n###小节"), "<p>正文</p>\n<h3>小节</h3>");
  assert.equal(body("1.第一\n2.第二"), "<ol>\n<li>第一</li>\n<li>第二</li>\n</ol>");
  assert.equal(body("1. 第一\n2.第二"), "<ol>\n<li>第一</li>\n<li>第二</li>\n</ol>");
  assert.equal(body("-第一\n-第二"), "<ul>\n<li>第一</li>\n<li>第二</li>\n</ul>");
  assert.equal(body("#话题#今天"), "<p>#话题#今天</p>");
  assert.equal(body("1.5倍速度"), "<p>1.5倍速度</p>");
  assert.equal(body("1、第一\n2、第二"), "<p>1、第一<br>2、第二</p>");
  assert.equal(body("```\n##不是标题\n1.不是列表\n```"), "<pre><code>##不是标题\n1.不是列表\n</code></pre>");
});

test("dollars in prices stay text; formulas are typeset", () => {
  const paint = (src) => { const host = document.createElement("div"); host.innerHTML = renderMd(src, { cache: false }); typesetMath(host); return host; };
  for (const price of ["价格 $5，折后 $3，很便宜", "原价$100，现价$80", "from $5 to $10"]) {
    const host = paint(price);
    assert.equal(host.querySelector(".md-math"), null, price);
    assert.equal(host.textContent.trim(), price);
  }
  const formulas = paint("设$x$为变量，$y=2x$。");
  assert.equal(formulas.querySelectorAll(".md-math .katex").length, 2);
  assert.equal(paint("$$\na^2+b^2\n$$").querySelector('.md-math[data-display="true"] .katex') !== null, true);
  assert.equal(paint("`echo $HOME $PATH`").querySelector("code").textContent, "echo $HOME $PATH");
});
