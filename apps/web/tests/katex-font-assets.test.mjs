import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import postcss from "postcss";
import woff2Only from "../scripts/postcss-katex-fonts.cjs";
const require = createRequire(import.meta.url);

test("local KaTeX retains every WOFF2 face while omitting obsolete fallback downloads", async () => {
  const original = readFileSync(require.resolve("katex/dist/katex.min.css"), "utf8");
  const result = await postcss([woff2Only()]).process(original, { from: undefined });
  const urls = (css) => [...css.matchAll(/url\(([^)]*\.woff2)\)/g)].map((match) => match[1]);
  assert.ok(urls(original).length > 10);
  assert.deepEqual(urls(result.css), urls(original));
  assert.doesNotMatch(result.css, /\.woff\)|\.ttf\)/);
  assert.equal((result.css.match(/@font-face/g) ?? []).length, (original.match(/@font-face/g) ?? []).length);
});

test("other fonts and faces without WOFF2 remain unchanged", async () => {
  const css = '@font-face{font-family:Other;src:url(other.woff)}@font-face{font-family:KaTeX_Unknown;src:url(old.ttf)}';
  assert.equal((await postcss([woff2Only()]).process(css, { from: undefined })).css, css);
});
