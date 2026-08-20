import assert from "node:assert/strict";
import test from "node:test";

import {
  ACCENT_PRESETS,
  LEGACY_THEME_PREFERENCES,
  THEME_DEFAULT_ACCENTS,
  THEME_IDS,
  THEME_STYLES,
  THEME_STYLE_PAIRS,
  accentOverrideTokens,
  coerceAccentColor,
  coerceThemeStyle,
  deriveAccentHover,
  legacyThemePreference,
  migrateAppearancePreferences,
  resolveThemePreference,
  rewriteCustomCssForOverlay,
} from "../lib/prefs/theme-config.ts";

test("color style packages are Beige / Neutral / Aurora only", () => {
  assert.deepEqual([...THEME_STYLES], ["beige", "neutral", "aurora"]);
  assert.equal("custom" in THEME_STYLE_PAIRS, false);
});

test("every color style has distinct light and dark theme ids", () => {
  for (const [style, pair] of Object.entries(THEME_STYLE_PAIRS)) {
    assert.notEqual(
      pair.light,
      pair.dark,
      `${style} must resolve light and dark to different data-theme values`,
    );
    assert.ok(THEME_IDS.includes(pair.light), `${style} light id must be registered`);
    assert.ok(THEME_IDS.includes(pair.dark), `${style} dark id must be registered`);
  }
});

test("stored custom style coerces to beige and resolves the beige pair", () => {
  assert.equal(coerceThemeStyle("custom"), "beige");
  assert.deepEqual(resolveThemePreference("custom", "dark", false), {
    theme: "beige-dark",
    resolvedMode: "dark",
  });
  assert.deepEqual(resolveThemePreference("custom", "light", true), {
    theme: "beige-light",
    resolvedMode: "light",
  });
  assert.equal(resolveThemePreference("custom", "auto", false).theme, "beige-light");
  assert.equal(resolveThemePreference("custom", "auto", true).theme, "beige-dark");
});

test("legacy custom prefs migrate onto beige, keeping the saved mode", () => {
  assert.deepEqual(legacyThemePreference("custom", "2"), {
    style: "beige",
    mode: "dark",
  });
  assert.deepEqual(legacyThemePreference("custom-light", "3"), {
    style: "beige",
    mode: "light",
  });
  assert.equal(LEGACY_THEME_PREFERENCES.custom.style, "beige");
  assert.equal(LEGACY_THEME_PREFERENCES["custom-light"].mode, "light");
});

test("custom style + saved CSS migrates to beige and turns the overlay on", () => {
  const next = migrateAppearancePreferences({
    schema: "3",
    style: "custom",
    mode: "light",
    legacy: "custom-light",
    customCss: '[data-theme="custom"] { --bg-primary: #111; }',
  });
  assert.equal(next.style, "beige");
  assert.equal(next.mode, "light");
  assert.equal(next.customCssEnabled, true);
  assert.equal(next.migratedFromCustom, true);
});

test("custom style without CSS migrates to beige and leaves overlay off", () => {
  const next = migrateAppearancePreferences({
    schema: "3",
    style: "custom",
    mode: "dark",
    legacy: "custom",
    customCss: "   ",
  });
  assert.equal(next.style, "beige");
  assert.equal(next.customCssEnabled, false);
});

test("saved CSS with no enable flag turns the overlay on", () => {
  const next = migrateAppearancePreferences({
    schema: "3",
    style: "beige",
    mode: "auto",
    customCss: "html { --bg-primary: #111; }",
  });
  assert.equal(next.customCssEnabled, true);
  assert.equal(next.style, "beige");
});

test("explicit overlay off stays off after beige is already stored", () => {
  const next = migrateAppearancePreferences({
    schema: "3",
    style: "beige",
    mode: "dark",
    legacy: "beige-dark",
    customCss: "html { color: red; }",
    customCssEnabled: "0",
  });
  assert.equal(next.customCssEnabled, false);
});

test("accent empty means package default; override writes the three tokens", () => {
  assert.equal(coerceAccentColor(""), null);
  assert.equal(coerceAccentColor("   "), null);
  assert.equal(coerceAccentColor("not-a-color"), null);
  assert.equal(coerceAccentColor("#d97757"), "#d97757");
  assert.equal(coerceAccentColor("#ABC"), "#aabbcc");
  assert.equal(THEME_DEFAULT_ACCENTS["beige-dark"], "#d97757");
  const tokens = accentOverrideTokens("#d97757");
  assert.equal(tokens["--accent-orange"], "#d97757");
  assert.equal(tokens["--accent-fill"], "#d97757");
  assert.equal(tokens["--accent-orange-hover"], deriveAccentHover("#d97757"));
  assert.notEqual(tokens["--accent-orange-hover"], "#d97757");
  assert.ok(ACCENT_PRESETS.includes(THEME_DEFAULT_ACCENTS.aurora));
});

test("old custom theme selectors keep working as overlay rules", () => {
  const out = rewriteCustomCssForOverlay([
    '[data-theme="custom"] { color: red; }',
    '[data-theme="custom-light"] { color: blue; }',
  ].join(" "));
  assert.match(out, /\[data-custom-css="on"\]\[data-theme-mode="dark"\] \{ color: red; \}/);
  assert.match(out, /\[data-custom-css="on"\]\[data-theme-mode="light"\] \{ color: blue; \}/);
  assert.doesNotMatch(out, /data-theme="custom"/);
});
