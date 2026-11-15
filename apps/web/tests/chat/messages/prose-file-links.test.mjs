/**
 * Paths in a settled reply become links once the worker confirms them; a
 * click opens the preview, and a link to a local path never reaches the
 * app's router.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { installLoader, STUBS } from "../../_support/load.mjs";
import { installDom, render } from "../../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/lib/files/files-shared": "export const useCurrentProject = () => ({ id: 'p1', path: '/work/repo' });",
    "@/lib/files/open-local-file": "export const openLocalFile = (...args) => { (globalThis.__opened ??= []).push(args); return true; };",
    "@/lib/format-utils/toast": "export const showToast = (message) => { (globalThis.__toasts ??= []).push(message); };",
    "@/components/files/file-path-menu": "export const FilePathMenuContent = () => null;",
  },
});
installDom({ pathname: "/chat" });
globalThis.MutationObserver ??= window.MutationObserver ?? class { observe() {} disconnect() {} };

const { createElement } = await import("react");
const { resetFilePathLookup } = await import("../../../lib/files/file-path-lookup.ts");
const { ProseFileLinks } = await import("../../../components/chat/messages/bubbles/prose-file-links.tsx");

const REPLY = '<span class="md-rendered"><p>Saved <code>exports/plan.pptx</code>, see '
  + '<a href="/work/repo/notes.md:4">notes</a>, <a href="/s/s-1#m-2">source</a>, '
  + '<code>npm run build</code> and <code>gone.pdf</code>.</p>'
  + '<pre><code>exports/plan.pptx</code></pre></span>';

const asked = [];
resetFilePathLookup(async (url) => {
  const paths = new URL(url, "http://worker").searchParams.getAll("path");
  asked.push(...paths);
  const known = { "exports/plan.pptx": "/work/repo/exports/plan.pptx", "/work/repo/notes.md": "/work/repo/notes.md" };
  return new Response(JSON.stringify({ files: Object.fromEntries(paths.map(p => [p, known[p] ?? null])) }));
});

const settle = () => new Promise(resolve => setTimeout(resolve, 20));

test("a settled reply links the files the worker confirmed, and only those", async () => {
  const view = await render(createElement(ProseFileLinks, { sessionId: "s1", settled: true },
    createElement("div", { className: "chat-stream-body", dangerouslySetInnerHTML: { __html: REPLY } })));
  await view.act(settle);
  assert.deepEqual(asked.sort(), ["/work/repo/notes.md", "exports/plan.pptx", "gone.pdf"], "one question per path-shaped string; app links, commands and code blocks are not asked about");
  const links = [...view.container.querySelectorAll(".md-file-link")];
  assert.deepEqual(links.map(link => link.dataset.filePath), ["/work/repo/exports/plan.pptx", "/work/repo/notes.md"]);
  assert.ok(links.every(link => !link.hasAttribute("href") && link.getAttribute("role") === "link"));
  assert.equal(links[0].querySelector("code")?.textContent, "exports/plan.pptx", "the code span keeps its look inside the link");
  assert.equal(view.container.querySelector('a[href="/s/s-1#m-2"]')?.className ?? "", "", "a link to a page of the app stays a link");

  links[0].querySelector("code").click();
  assert.deepEqual(globalThis.__opened.at(-1).slice(0, 2), ["/work/repo/exports/plan.pptx", "s1"]);
  await view.unmount();
});

test("a streaming reply is not scanned", async () => {
  asked.length = 0;
  const view = await render(createElement(ProseFileLinks, { sessionId: "s1", settled: false },
    createElement("div", { dangerouslySetInnerHTML: { __html: '<span class="md-rendered"><code>other/deck.pptx</code></span>' } })));
  await view.act(settle);
  assert.deepEqual(asked, []);
  await view.unmount();
});

test("a markdown link to a local path is never routed, even before it is confirmed", async () => {
  await import("../../../components/chat/messages/bubbles/markdown.ts");
  const view = await render(createElement(ProseFileLinks, { sessionId: "s2", settled: false },
    createElement("div", { dangerouslySetInnerHTML: { __html: '<span class="md-rendered"><a href="/tmp/missing.docx">doc</a> <a href="/settings">settings</a></span>' } })));
  const [file, page] = view.container.querySelectorAll("a");
  const click = (element) => {
    const event = new window.Event("click", { bubbles: true, cancelable: true });
    element.dispatchEvent(event);
    return event.defaultPrevented;
  };
  assert.equal(click(file), true, "the file link is stopped before the router");
  assert.equal(click(page), false, "a page of the app still navigates");
  await view.act(settle);
  assert.deepEqual(globalThis.__toasts, ["This file isn't available."]);
  await view.unmount();
});

test("a bare href not confirmed as a file keeps its own click and says nothing", async () => {
  globalThis.__toasts = [];
  asked.length = 0;
  const view = await render(createElement(ProseFileLinks, { sessionId: "s3", settled: true },
    createElement("div", { dangerouslySetInnerHTML: { __html: '<span class="md-rendered"><a href="docs.example.ai">docs</a> <a href="example.com/path">x</a></span>' } })));
  await view.act(settle);
  assert.deepEqual(asked.sort(), ["docs.example.ai", "example.com/path"], "asked about once, in the background");
  for (const anchor of view.container.querySelectorAll("a")) {
    assert.equal(anchor.classList.contains("md-file-link"), false, "the worker did not confirm it");
    const event = new window.Event("click", { bubbles: true, cancelable: true });
    anchor.dispatchEvent(event);
    assert.equal(event.defaultPrevented, false, anchor.getAttribute("href"));
  }
  await view.act(settle);
  assert.deepEqual(globalThis.__toasts, []);
  await view.unmount();
});

// A file link in a code span was told apart from plain code only by its hue,
// and in the warm themes the link colour (#c15f3c) is next to the code red
// (#c24e42): at rest the two looked the same. A file link carries the app's
// rendered-markdown link underline (the preview's `.document a`), in prose
// and in a code span, and changes nothing that moves the text.
test("a file link is underlined at rest like the app's markdown links, without moving the text", async () => {
  const { readFileSync } = await import("node:fs");
  const read = (path) => readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8");
  const declarations = (css, selector) => {
    const rule = new RegExp(`(?:^|\\})\\s*${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`, "m").exec(css.replace(/\/\*[\s\S]*?\*\//g, ""));
    assert.ok(rule, `rule ${selector}`);
    return Object.fromEntries(rule[1].split(";").map((d) => d.split(":").map((s) => s.trim())).filter(([k, v]) => k && v));
  };
  const fileLinks = read("app/styles/chat/messages/file-links.css");
  const preview = declarations(read("components/files/preview/markdown-preview.module.css"), ".document a");
  const link = declarations(fileLinks, ".message-content a.md-file-link");
  for (const key of ["text-decoration", "text-decoration-color", "text-underline-offset"]) {
    assert.equal(link[key], preview[key], `${key} matches the markdown link`);
  }
  assert.equal(declarations(fileLinks, ".message-content a.md-file-link:hover")["text-decoration-color"], "currentColor");
  assert.equal(declarations(fileLinks, ".message-content a.md-file-link code").color, "var(--accent-blue)");
  const layout = /(^|-)(padding|margin|border|font|line-height|width|height|display|position|letter-spacing)/;
  for (const selector of [".message-content a.md-file-link", ".message-content a.md-file-link:hover", ".message-content a.md-file-link code"]) {
    const moved = Object.keys(declarations(fileLinks, selector)).filter((key) => layout.test(key));
    assert.deepEqual(moved, [], `${selector} changes no layout`);
  }
});
