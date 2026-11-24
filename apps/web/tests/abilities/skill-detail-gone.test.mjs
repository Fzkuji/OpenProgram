// A skill that is not there goes back to the list; a refused invocation
// trace leaves its field out instead of staying blank.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "next/link": "import { createElement } from 'react'; export default ({ href, children, prefetch, ...props }) => createElement('a', { href, ...props }, children);",
    "@/lib/session-store": "export const useSessionStore = (pick) => pick({ setComposerInput() {}, currentSessionId: null });",
    "@/lib/format-utils/markdown": "export const Markdown = () => null;",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
    "@/lib/nav/shallow-nav": "export const pushPath = () => {};",
  },
  replace: { "/components/ui/confirm-dialog.tsx": "export const confirmAction = async () => true;" },
});
installDom({ pathname: "/skills/gone" });

const { createElement } = await import("react");
const { SkillDetailPage } = await import("../../components/skills/skill-detail-page.tsx");
const { forgetKept } = await import("../../lib/abilities/kept-read.ts");

const answer = (status, body = {}) => new Response(JSON.stringify(body), { status });
const flush = (view) => view.act(() => new Promise((resolve) => setTimeout(resolve, 20)));

test("a skill that is not there goes back to the skills list", async () => {
  forgetKept();
  (globalThis.__routerCalls ??= []).length = 0;
  globalThis.fetch = async () => answer(404);
  const warn = console.warn;
  console.warn = () => {};
  try {
    const view = await render(createElement(SkillDetailPage, { name: "gone" }));
    await flush(view);
    assert.deepEqual(globalThis.__routerCalls, [["replace", "/skills"]]);
    await view.unmount();
  } finally { console.warn = warn; }
});

test("a refused invocation trace leaves the Invocations field out", async () => {
  forgetKept();
  (globalThis.__routerCalls ??= []).length = 0;
  const detail = { name: "here", description: "", source: "user", enabled: true, body: "", resources: [], category: "", optional: false };
  globalThis.fetch = async (url) => url.endsWith("/invoke-trace") ? answer(403) : answer(200, detail);
  const warn = console.warn;
  console.warn = () => {};
  try {
    const view = await render(createElement(SkillDetailPage, { name: "here" }));
    await flush(view);
    assert.deepEqual(globalThis.__routerCalls, []);
    assert.ok(view.container.textContent.includes("Enabled"));
    assert.ok(!view.container.textContent.includes("Invocations"));
    await view.unmount();
  } finally { console.warn = warn; }
});
