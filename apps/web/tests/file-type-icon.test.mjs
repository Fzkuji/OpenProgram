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
import { Python, TypeScript, Shell, BracketsYellow, Markdown, Git, Claude } from "@react-symbols/icons/files";
import { DefaultFileIcon } from "@react-symbols/icons/utils";

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../components/files/file-type-icon.tsx", import.meta.url))],
  bundle: true, write: false, format: "cjs", platform: "node", jsx: "automatic",
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
    "app.py": Python, "app.ts": TypeScript, "app.sh": Shell,
    "data.json": BracketsYellow, "README.md": Markdown, ".gitignore": Git,
    "CLAUDE.md": Claude, "file.unknown-extension": DefaultFileIcon,
  };
  for (const [name, Icon] of Object.entries(expected)) {
    const markup = renderToStaticMarkup(createElement(Icon, {
      width: 16, height: 16, "aria-hidden": "true", focusable: "false", style: { flexShrink: 0 },
    }));
    assert.equal(render(name), markup, name);
  }
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
