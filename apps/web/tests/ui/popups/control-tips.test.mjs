import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(`../../../${path}`, import.meta.url), "utf8");

test("provider detail sections never share a React key", () => {
  // Siblings keyed alike made React leave a second API Base URL block behind.
  const detail = read("components/settings/providers/detail.tsx");
  const keys = [...detail.matchAll(/key=\{`\$\{provider\.id\}:([a-z-]+)`\}/g)].map(m => m[1]);
  assert.deepEqual(keys, ["setup", "credentials", "base-url", "usage", "check", "models"]);
  assert.doesNotMatch(detail, /key=\{provider\.id\}/);
});

test("No element outside a control carries a native title", async () => {
  const { createRequire } = await import("node:module");
  const { readdirSync, statSync } = await import("node:fs");
  const ts = createRequire(import.meta.url)("typescript");
  const { fileURLToPath } = await import("node:url");
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const controls = new Set(["button", "a", "summary", "input", "select", "textarea", "iframe"]);
  const found = [];
  const walk = dir => {
    for (const name of readdirSync(dir)) {
      const path = dir + (dir.endsWith("/") ? "" : "/") + name;
      if (statSync(path).isDirectory()) walk(path);
      else if (path.endsWith(".tsx")) {
        const sf = ts.createSourceFile(path, readFileSync(path, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
        const visit = node => {
          if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && /^[a-z]/.test(node.tagName.getText(sf)) && !controls.has(node.tagName.getText(sf))) {
            const attrs = node.attributes.properties.filter(ts.isJsxAttribute);
            const role = attrs.find(a => a.name.getText(sf) === "role")?.initializer?.getText(sf) ?? "";
            if (attrs.some(a => a.name.getText(sf) === "title") && !/button|tab|menuitem|switch/.test(role)) found.push(`${path.slice(root.length)}:${sf.getLineAndCharacterOfPosition(node.getStart()).line + 1}`);
          }
          ts.forEachChild(node, visit);
        };
        visit(sf);
      }
    }
  };
  walk(root + "components");
  walk(root + "app");
  assert.deepEqual(found, []);
});

test("A reply's blocks carry no pairwise spacing compensation", () => {
  for (const f of ["messages/bubbles.css", "messages/message-actions.css", "execution/execution-strip.css", "messages/stream-blocks.css"]) {
    assert.doesNotMatch(read(`app/styles/chat/${f}`), /:has\(> \.md-rendered/);
  }
});

test("a control's keys show as key caps, not as text in its name", () => {
  for (const file of ["components/chat/dag/dag-tools.tsx", "components/chat/dag/dag-view.tsx"]) {
    assert.doesNotMatch(read(file), /data-tip=\{[^}]*(· [A-Z?+−⇧]|\((⌘|⇧|Esc|⌫))/, file);
  }
});
