import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { CUSTOM_CSS_TEMPLATE, THEME_STYLES } from "../lib/prefs/theme-config.ts";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const settings = readFileSync(join(root, "components/settings/general-section.tsx"), "utf8");
const css = readFileSync(join(root, "components/settings/settings-page.module.css"), "utf8");
const picker = readFileSync(join(root, "components/avatar/AvatarPicker.tsx"), "utf8");

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

test("appearance chrome uses the sans control column, not mono .value", () => {
  assert.match(css, /\.value\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
  assert.match(css, /\.control\s*\{[^}]*font-size:\s*14px[^}]*font-family:\s*var\(--font-sans\)/s);
  assert.match(css, /\.themeCardLabel\s*\{[^}]*font-size:\s*14px[^}]*font-family:\s*var\(--font-sans\)/s);
  assert.match(css, /\.customCssEnable\s*\{[^}]*font-size:\s*var\(--fs-sm\)[^}]*font-family:\s*var\(--font-sans\)/s);
  assert.match(css, /\.customCssHint\s*\{[^}]*font-size:\s*var\(--fs-sm\)[^}]*color:\s*var\(--text-muted\)[^}]*font-family:\s*var\(--font-sans\)/s);
  assert.match(css, /\.customCssArea\s*\{[^}]*font-family:\s*var\(--font-mono\)/s);
  assert.match(css, /\.settingsAction\s*\{[^}]*font-size:\s*var\(--fs-base\)[^}]*font-family:\s*var\(--font-sans\)/s);

  assert.match(settings, /styles\.control \+ " " \+ styles\.valueWide/);
  assert.doesNotMatch(settings, /styles\.value \+ " " \+ styles\.valueWide/);
  assert.match(settings, /styles\.value\}>\{updateState\?\.currentVersion/);
  assert.match(settings, /styles\.value\}>Agentic Programming/);
  assert.match(settings, /<code>openprogram upgrade --check<\/code>/);
  assert.match(settings, /className=\{"text-fs-base " \+ styles\.settingsAction\}/);
  assert.match(settings, /style: \{ fontFamily: fontStack\(font\) \}/);
});

test("avatar picker on General uses the 13px sans floor", () => {
  assert.match(picker, /fontSize: "var\(--fs-sm\)"/);
  assert.match(picker, /fontFamily: "var\(--font-sans\)"/);
  assert.match(picker, /text-fs-sm/);
  assert.doesNotMatch(picker, /text-\[12px\]/);
  assert.doesNotMatch(picker, /fontSize: 11/);
});
