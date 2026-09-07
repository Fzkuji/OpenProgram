import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { parseHTML } from "linkedom";
const require = createRequire(import.meta.url);
const bundle = await build({
  stdin: { contents: 'export * from "./components/files/explorer-header"; export * from "./components/files/file-management";', resolveDir: new URL("../", import.meta.url).pathname, loader: "tsx" },
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic", loader: { ".css": "empty" },
  plugins: [{ name: "host", setup(builder) {
    builder.onLoad({ filter: /\.css$/ }, () => ({ contents: "export default {};", loader: "js" }));
    builder.onResolve({ filter: /^react(?:-dom)?(?:\/.*)?$/ }, ({ path }) => ({ path: require.resolve(path), external: true }));
    builder.onResolve({ filter: /^@\/lib\/i18n$/ }, () => ({ path: "i18n", namespace: "fake" }));
    builder.onResolve({ filter: /^@\/lib\/net\/ws-request$/ }, () => ({ path: "ws", namespace: "fake" }));
    builder.onLoad({ filter: /.*/, namespace: "fake" }, ({ path }) => ({ contents: path === "i18n" ? 'export const useTranslation=()=>({text:(en)=>en});' : 'export const wsRequest=async()=>null;', loader: "js" }));
  } }],
});
const temporary = mkdtempSync(join(tmpdir(), "op-file-management-"));
let api;
try {
  const output = join(temporary, "module.cjs"); writeFileSync(output, bundle.outputFiles[0].text); api = require(output);
} finally { rmSync(temporary, { recursive: true, force: true }); }
const noop = () => {};
const props = { rootName: "Project", rootPath: "/project", searchOpen: true, onSearchOpenChange: noop, query: "", onQueryChange: noop, mode: "filter", onModeChange: noop, fuzzy: true, onFuzzyChange: noop, resultCount: 0, resultIndex: 0, onMoveResult: noop };

test("Files path, actions and expanded search occupy separate ordered rows", () => {
  const markup = renderToStaticMarkup(h(api.ExplorerHeader, { ...props, pathNavigation: h("nav", { "data-path": true }, "Project / src"), actions: h("button", { "data-action": true }, "Sort") }));
  const { document } = parseHTML(markup);
  const header = document.firstElementChild;
  assert.equal(header.children.length, 3);
  assert.ok(header.children[0].querySelector("nav"));
  assert.ok(header.children[1].querySelector("[data-action]"));
  assert.ok(header.children[2].querySelector('input[aria-label="Search"]'));
  assert.equal(header.children[0].querySelector("input"), null);
  assert.equal(document.querySelectorAll('input').length, 1);
});

test("shared Programs header retains its single toolbar without a path row", () => {
  const { document } = parseHTML(renderToStaticMarkup(h(api.ExplorerHeader, { ...props, showRootPath: false, leading: h("span", { "data-leading": true }, "Programs") })));
  assert.equal(document.firstElementChild.children.length, 2);
  assert.ok(document.firstElementChild.children[0].querySelector("[data-leading]"));
});

test("narrow breadcrumb keeps project root and current filename", () => {
  const { document } = parseHTML(renderToStaticMarkup(h(api.FileBreadcrumb, { root: "Project", path: "src/deep/file2.py", onLocate: noop })));
  assert.match(document.firstElementChild.textContent, /Project/);
  assert.match(document.firstElementChild.textContent, /file2.py/);
  assert.ok(document.querySelector('button[title="Parent folders"]'));
  assert.equal(document.querySelector('button[title="src/deep/file2.py"]').textContent, "file2.py");
});

test("byte display covers zero and binary unit boundaries", () => {
  assert.equal(api.formatFileBytes(0), "0 B");
  assert.equal(api.formatFileBytes(1023), "1023 B");
  assert.equal(api.formatFileBytes(1024), "1.0 KiB");
  assert.equal(api.formatFileBytes(1024 ** 3), "1.0 GiB");
});
