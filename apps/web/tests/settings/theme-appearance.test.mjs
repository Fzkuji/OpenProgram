import assert from "node:assert/strict";
import test from "node:test";

import { CUSTOM_CSS_TEMPLATE, THEME_STYLES } from "../../lib/prefs/appearance/theme-config.ts";

test("the color style packages have no Custom package", () => {
  assert.deepEqual([...THEME_STYLES], ["beige", "neutral", "aurora", "vivid"]);
});

test("snippet template targets html / current packages, not a Custom skin", () => {
  assert.match(CUSTOM_CSS_TEMPLATE, /html\[data-theme="beige-dark"\]/);
  assert.match(CUSTOM_CSS_TEMPLATE, /html\[data-theme="beige-light"\]/);
  assert.match(CUSTOM_CSS_TEMPLATE, /You can still target any data-theme/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-orange:/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-fill:/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-orange-hover:/);
  assert.doesNotMatch(CUSTOM_CSS_TEMPLATE, /\[data-theme="custom"\]/);
});
