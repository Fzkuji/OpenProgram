import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import ts from "typescript";

test("verification scroll updates visible state without persisting temporary position", () => {
  const source = readFileSync(new URL("../components/chat/messages/message-list.tsx", import.meta.url), "utf8");
  const file = ts.createSourceFile("message-list.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const owner = file.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "useChatAreaStick");
  let callback;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(file) === "onScroll") callback = node.initializer;
    ts.forEachChild(node, visit);
  }
  visit(owner);
  assert.ok(callback, "real conversation scroll listener exists");
  let marked = true, persisted = 0, synced = 0;
  const position = { current: 0 };
  const handler = vm.runInNewContext(ts.transpileModule(`(${callback.getText(file)})`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, { area: { clientHeight: 600, scrollTop: 400, hasAttribute: () => marked },
    syncDetached: () => { synced++; }, scrollTopRef: position, activeKeyRef: { current: "p1" },
    writeChatScroll: () => { persisted++; }, window: { sessionStorage: {} } });
  handler();
  assert.equal(persisted, 0);
  assert.equal(position.current, 400);
  marked = false;
  handler();
  assert.equal(persisted, 1);
  assert.equal(synced, 2);
});

test("perspective verification suppresses every center-tab persistence writer while marked", () => {
  const source = readFileSync(new URL("../lib/state/center-tabs-persistence.ts", import.meta.url), "utf8");
  const file = ts.createSourceFile("persistence.ts", source, ts.ScriptTarget.Latest, true);
  const fn = file.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === "persistCenterTabsPayload");
  assert.ok(fn);
  let marked = true;
  const stored = new Map();
  const context = { exports: {}, window: {}, document: { getElementById: () => ({ hasAttribute: () => marked }) },
    centerTabsStorageKey: () => "tabs", normalizeCenterTabsPayload: (value) => value, pendingTransfers: () => [],
    localStorage: { setItem: (key, value) => stored.set(key, value), getItem: (key) => stored.get(key) } };
  vm.runInNewContext(ts.transpileModule(fn.getText(file), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, context);
  const persist = context.exports.persistCenterTabsPayload;
  assert.equal(persist({ tabs: [{ id: "s:p1", dagView: true }] }), false);
  assert.equal(stored.size, 0);
  marked = false;
  assert.equal(persist({ tabs: [{ id: "s:p1", dagView: false }] }), true);
  assert.equal(JSON.parse(stored.get("tabs")).tabs[0].dagView, false);
});

test("native perspective target names the real session toggle and invokes its actual handler", () => {
  const source = readFileSync(new URL("../components/chat/view-controls.tsx", import.meta.url), "utf8");
  const file = ts.createSourceFile("view-controls.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let button;
  function visit(node) {
    if (ts.isJsxOpeningElement(node) && node.tagName.getText(file) === "button" &&
        node.attributes.properties.some((attr) => attr.name?.getText(file) === "id" && attr.initializer?.text === "sessionPerspectiveToggle")) button = node;
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(button);
  const attrs = Object.fromEntries(button.attributes.properties.map((attr) => [attr.name.getText(file), attr.initializer]));
  assert.equal(attrs["data-session-id"].expression.getText(file), "sessionId");
  assert.equal(attrs["data-tab-id"].expression.getText(file), "activeId");
  const calls = [];
  const handler = vm.runInNewContext(ts.transpileModule(`(${attrs.onClick.expression.getText(file)})`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, { activeId: "s:p1", dagView: false, setTabDagView: (...args) => calls.push(args) });
  handler();
  assert.deepEqual(calls, [["s:p1", true]]);
});
