import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { pathToFileURL, fileURLToPath } from "node:url";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const require = createRequire(import.meta.url);
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../../components/files/file-type-icon.tsx", import.meta.url))],
  loader: { ".module.css": "empty" }, bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic",
  plugins: [{ name: "shared-react", setup(builder) {
    builder.onResolve({ filter: /^react(?:\/.*)?$/ }, ({ path }) => ({ path: require.resolve(path), external: true }));
  } }],
});
const temporary = mkdtempSync(join(tmpdir(), "op-file-icons-"));
let FileTypeIcon;
try {
  const output = join(temporary, "icons.cjs");
  writeFileSync(output, bundle.outputFiles[0].text);
  ({ FileTypeIcon } = await import(pathToFileURL(output).href));
} finally {
  rmSync(temporary, { recursive: true, force: true });
}
const render = (name) => renderToStaticMarkup(createElement(FileTypeIcon, { name }));

test("file type icons distinguish common languages and normalize paths", () => {
  const expected = {
    "app.py": "python", "app.ts": "typescript", "app.sh": "bash",
    "data.json": "json", "README.md": "markdown", ".gitignore": "git",
    "CLAUDE.md": "claude", "file.unknown-extension": "default",
  };
  for (const [name, token] of Object.entries(expected)) {
    assert.match(render(name), new RegExp(`data-file-icon="${token}"`), name);
  }
  assert.match(render(".DS_Store"), /M8 1v3a3 3 0 0 0 3 3h3/);
  assert.match(render("README.md"), /M1 12V4h2l2 2.5L7 4/);
  assert.equal(render("C:\\src.v2\\APP.PY"), render("app.py"));
  assert.equal(render("src.v2/nested/app.ts"), render("app.ts"));
  assert.equal(render("docs/AGENTS.md"), render("README.md"));
  assert.notEqual(render("CLAUDE.md"), render("README.md"));
  assert.equal(render(".gitattributes"), render(".gitignore"));
  assert.equal(render("file.unknown-extension"), render(".DS_Store"));
  for (const name of ["constructor", "__proto__", "file.constructor", "file.__proto__"]) {
    assert.equal(render(name), render("file.unknown-extension"), name);
  }
  assert.equal(render("constructor.py"), render("app.py"));
  assert.match(render("app.py"), /aria-hidden="true"/);
  assert.match(render("app.py"), /width="16"/);
});

test("file icons preserve sizing and never render filename markup", () => {
  const markup = renderToStaticMarkup(createElement(FileTypeIcon, { name: "evil<svg onload=alert(1)>.py", size: 14, className: "tab-icon" }));
  assert.match(markup, /width="14"/);
  assert.match(markup, /tab-icon/);
  assert.doesNotMatch(markup, /onload|evil/);
});

test("repeated gradient icons have independent SVG paint references", () => {
  const markup = renderToStaticMarkup(createElement("div", {},
    createElement(FileTypeIcon, { name: "next.config.js" }),
    createElement(FileTypeIcon, { name: "next.config.js" })));
  const ids = [...markup.matchAll(/id="([^"]+)"/g)].map(match => match[1]);
  const refs = [...markup.matchAll(/url\(#([^)]+)\)/g)].map(match => match[1]);
  assert.ok(ids.length >= 2);
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual(refs, ids);
});
