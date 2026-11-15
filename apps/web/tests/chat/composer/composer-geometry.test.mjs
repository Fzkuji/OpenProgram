import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// The input box's corner buttons and chip rows must not hang on the
// shadcn Button size classes: a Button restyle once shrank the mic and
// send buttons from 28px to 24.5px while their offsets stayed tuned for
// 28px, so both sat high in the text line.
const root = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (path) => readFileSync(join(root, path), "utf8");
const composer = read("components/chat/composer/composer.module.css");
const voice = read("components/chat/composer/voice/voice.module.css");

const rule = (css, selector) => {
  const at = css.indexOf(`${selector} {`);
  assert.notEqual(at, -1, `missing ${selector}`);
  return css.slice(at, css.indexOf("}", at));
};

test("mic and send take their size and place from the tokens", () => {
  for (const [css, selector, side] of [
    [composer, ".actionBtn", "right"],
    [voice, ".micBtn", "left"],
  ]) {
    const body = rule(css, selector);
    assert.match(body, /width: var\(--composer-action-size\)/);
    assert.match(body, /height: var\(--composer-action-size\)/);
    assert.match(body, /top: var\(--composer-action-top\)/);
    assert.match(body, new RegExp(`${side}: var\\(--composer-action-inset\\)`));
  }
});
