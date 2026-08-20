import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { CUSTOM_CSS_TEMPLATE, THEME_STYLES } from "../lib/prefs/theme-config.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const settings = readFileSync(join(root, "components/settings/general-section.tsx"), "utf8");

test("settings color style grid has three packages and no Custom card", () => {
  assert.deepEqual([...THEME_STYLES], ["beige", "neutral", "aurora"]);
  assert.match(settings, /THEME_STYLES\.map/);
  assert.doesNotMatch(settings, /custom:\s*text\("Custom"/);
  assert.doesNotMatch(settings, /pick the "Custom" color style/);
  assert.doesNotMatch(settings, /选择「自定义」颜色风格/);
});

test("settings appearance has accent picker, presets, and reset", () => {
  assert.match(settings, /type="color"/);
  assert.match(settings, /ACCENT_PRESETS/);
  assert.match(settings, /Reset to theme default/);
  assert.match(settings, /重置为主题默认/);
  assert.match(settings, /Accent color/);
  assert.match(settings, /强调色/);
});

test("settings CSS overlay has enable, insert template, and clear", () => {
  assert.match(settings, /Enable custom CSS/);
  assert.match(settings, /启用自定义 CSS/);
  assert.match(settings, /Insert template/);
  assert.match(settings, /插入模板/);
  assert.match(settings, /Clear/);
  assert.match(settings, /清空/);
  assert.match(settings, /insertCustomCssTemplate/);
  assert.match(settings, /clearCustomCss/);
  assert.match(settings, /Overlay on the current theme package/);
  assert.match(settings, /叠加在当前主题包之上/);
});

test("snippet template targets html / current packages, not a Custom skin", () => {
  assert.match(CUSTOM_CSS_TEMPLATE, /html\[data-theme="beige-dark"\]/);
  assert.match(CUSTOM_CSS_TEMPLATE, /html\[data-theme="beige-light"\]/);
  assert.match(CUSTOM_CSS_TEMPLATE, /You can still target any data-theme/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-orange:/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-fill:/);
  assert.match(CUSTOM_CSS_TEMPLATE, /--accent-orange-hover:/);
  assert.doesNotMatch(CUSTOM_CSS_TEMPLATE, /\[data-theme="custom"\]/);
  assert.match(settings, /CUSTOM_CSS_TEMPLATE/);
});
