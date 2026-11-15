import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { scratchDir } from "../../scratch-dir.mjs";

// The task list drawn under a turn's reply (components/chat/messages/cards/
// todo-plan-card.tsx), rendered with the store it reads stubbed.
const web = dirname(fileURLToPath(new URL("../../../package.json", import.meta.url)));
const dir = scratchDir(web, "todo-plan-card");
const file = join(dir, "card.mjs");
await build({
  absWorkingDir: web, entryPoints: ["components/chat/messages/cards/todo-plan-card.tsx"],
  external: ["react"], bundle: true, platform: "node", format: "esm", packages: "external", jsx: "automatic", outfile: file,
  plugins: [{ name: "card-services", setup(b) {
    b.onResolve({ filter: /^@\/lib\/session-store$|^@\/lib\/i18n$|^@\/components\/solar-icons$|^@\/components\/ui\/spinner$|\.module\.css$/ }, (a) => ({ path: a.path, namespace: "stub" }));
    b.onResolve({ filter: /^@\/lib\/chat\/transcript\/todo-plan$/ }, () => ({ path: join(web, "lib/chat/transcript/todo-plan.ts") }));
    b.onLoad({ filter: /.*/, namespace: "stub" }, (a) => ({ resolveDir: web, contents:
      a.path.endsWith(".module.css") ? "export default new Proxy({}, { get: (_, key) => String(key) });"
        : a.path.includes("session-store") ? "export const useSessionStore = (selector) => selector(globalThis.cardState); export const selectRow = (view, id) => (state) => state.rowsById[id];"
          : a.path.includes("spinner") ? 'import { createElement } from "react"; export const Spinner = (props) => createElement("span", { ...props, "data-glyph": "spinner" });'
          : a.path.includes("i18n") ? "export const useTranslation = () => ({ text: (en) => en });"
            : 'import { createElement } from "react"; export const SolarIcon = ({ name, motionPreset, size, ...rest }) => createElement("i", { "data-glyph": name, ...rest });',
    }));
  } }],
});
const { TodoPlans } = await import(pathToFileURL(file));
const { normalizePlans } = await import("../../../lib/chat/transcript/todo-plan.ts");

const plan = (statuses) => normalizePlans([{
  anchor_id: "t1_reply", plan_id: "1",
  todos: statuses.map((status, i) => ({ id: String(i + 1), subject: `step ${i + 1}`, status })),
}]);

function render(status, statuses, { terminal = false, latest = true, agents = 0 } = {}) {
  globalThis.cardState = {
    rowsById: { t1_reply: { id: "t1_reply", role: "assistant", content: "", status,
      attachCards: Array.from({ length: agents }, () => ({ attach: { status: "running" } })) } },
    executionUpdateOrders: terminal ? { e1: { sessionId: "s1", messageIds: ["t1_reply"], terminal: true } } : {},
  };
  return renderToStaticMarkup(createElement(TodoPlans, { sessionId: "s1", rowId: "t1_reply", plans: plan(statuses), latest }));
}

test("while its turn runs the list is open, with no header line, and the current step turns", () => {
  const html = render("running", ["completed", "in_progress", "pending"]);
  assert.match(html, /data-live=""/);
  assert.doesNotMatch(html, /<button/, "no folded line");
  assert.doesNotMatch(html, /Tasks \d/, "no small header");
  assert.match(html, /data-status="in_progress" data-live=""/, "the in-progress step spins");
  assert.match(html, /step 1.*step 2.*step 3/s);
});

test("a turn cut short keeps its list, with the current step on hold", () => {
  for (const status of ["cancelled", "interrupted", "error"]) {
    const html = render(status, ["completed", "in_progress", "pending"]);
    assert.equal([...html.matchAll(/<li /g)].length, 3);
    assert.match(html, /lucide-circle-ellipsis/);
    assert.doesNotMatch(html, /data-live|data-glyph="spinner"/, "nothing spins once the turn stopped");
  }
});

test("a list goes when its turn ends normally or the user writes again", () => {
  assert.equal(render("done", ["completed", "pending"]), "");
  assert.equal(render("cancelled", ["completed", "completed"]), "");
  assert.equal(render("cancelled", ["in_progress", "pending"], { latest: false }), "");
  assert.match(render("running", ["in_progress"], { latest: false }), /data-live=""/, "a running turn always shows");
});

test("a turn that ended with sub-agents running keeps its list and counts them", () => {
  const html = render("done", ["completed", "in_progress", "pending"], { agents: 2 });
  assert.match(html, /2 running tasks/);
  assert.match(html, /lucide-circle-ellipsis/);
  const working = render("running", ["in_progress"], { agents: 2 });
  assert.match(working, /data-glyph="spinner"/);
  assert.doesNotMatch(working, /running task/, "a working turn only spins");
});

test("done steps are not struck through and steps to come use the dashed circle", () => {
  const css = readFileSync(join(web, "components/chat/messages/cards/todo-plan-card.module.css"), "utf8");
  assert.doesNotMatch(css, /line-through|halted/);
  assert.match(render("running", ["pending"]), /lucide-circle-dashed/);
});

test("long plans show twelve steps with counts either side", () => {
  const statuses = Array.from({ length: 20 }, (_, i) => (i < 9 ? "completed" : i === 9 ? "in_progress" : "pending"));
  const html = render("running", statuses);
  assert.equal([...html.matchAll(/<li /g)].length, 12);
  assert.match(html, /8 earlier steps/);
  assert.doesNotMatch(html, / more to go/, "20 steps from 8 reach the end");
});

test("a list that goes away folds up, and reduced motion skips it", () => {
  const css = readFileSync(join(web, "components/chat/messages/cards/todo-plan-card.module.css"), "utf8");
  assert.match(css, /todo-exit 400ms/);
  assert.match(css, /prefers-reduced-motion[\s\S]*\.exit \{ animation: none; \}/);
  assert.match(readFileSync(join(web, "components/chat/messages/cards/todo-plan-card.tsx"), "utf8"), /EXIT_MS = 400/);
});

test("the list has no native title and no hover tip", () => {
  const html = render("cancelled", ["pending"]);
  assert.doesNotMatch(html, /\stitle=/);
  assert.doesNotMatch(readFileSync(join(web, "components/chat/messages/cards/todo-plan-card.tsx"), "utf8"), /Tooltip|title=/);
});

test("the transcript places lists per reply, never as one trailing card", () => {
  const list = readFileSync(join(web, "components/chat/messages/list/transcript-rows.tsx"), "utf8");
  assert.doesNotMatch(list, /<TodoPlanCard/);
  assert.match(list, /addTodoPlans\(\[id\]\)/);
  assert.match(list, /addTodoPlans\(run\)/, "a compacted turn's list follows its fold");
  const row = readFileSync(join(web, "components/chat/messages/list/rows/message-rows.tsx"), "utf8");
  assert.match(row, /<TodoPlans sessionId=\{scope\.sessionId\} viewKey=\{viewKey\} rowId=\{msg\.id\} \/>/, "a sub-agent's own plan sits in its own conversation");
});

test("only a live step spins, and reduced motion stops it", () => {
  assert.match(render("running", ["in_progress"]), /data-glyph="spinner"/);
  assert.doesNotMatch(render("cancelled", ["in_progress"]), /data-glyph="spinner"/);
  const css = readFileSync(join(web, "components/chat/messages/cards/todo-plan-card.module.css"), "utf8");
  assert.match(css, /prefers-reduced-motion[\s\S]*\.icon\[data-live\] \{ animation: none; \}/);
  assert.doesNotMatch(css, /box-shadow|todo-orbit/);
});
