import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("../app/styles/base.css", import.meta.url), "utf8");

test("a manually resized left sidebar can still collapse", () => {
  assert.match(
    css,
    /#sidebar\.sidebar\.collapsed\s*\{[^}]*width:\s*49px\s*!important;[^}]*min-width:\s*49px\s*!important;[^}]*max-width:\s*49px\s*!important;/s,
  );
});

test("left sidebar resizing preserves usable center space", () => {
  assert.match(
    css,
    /#sidebar:not\(\.collapsed\)\s*\{[^}]*max-width:\s*clamp\(180px,\s*calc\(100vw\s*-\s*360px\),\s*480px\);/s,
  );
});
