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
