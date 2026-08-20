import assert from "node:assert/strict";
import test from "node:test";

import {
  LEGACY_THEME_PREFERENCES,
  THEME_IDS,
  THEME_STYLE_PAIRS,
  legacyThemePreference,
  resolveThemePreference,
} from "../lib/prefs/theme-config.ts";

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

test("custom + light lands on custom-light, custom + dark stays custom", () => {
  assert.deepEqual(resolveThemePreference("custom", "dark", false), {
    theme: "custom",
    resolvedMode: "dark",
  });
  assert.deepEqual(resolveThemePreference("custom", "light", true), {
    theme: "custom-light",
    resolvedMode: "light",
  });
  assert.equal(resolveThemePreference("custom", "auto", false).theme, "custom-light");
  assert.equal(resolveThemePreference("custom", "auto", true).theme, "custom");
});

test("legacy custom prefs stay dark; custom-light maps to custom + light", () => {
  assert.deepEqual(legacyThemePreference("custom", "2"), {
    style: "custom",
    mode: "dark",
  });
  assert.deepEqual(legacyThemePreference("custom-light", "3"), {
    style: "custom",
    mode: "light",
  });
  assert.equal(LEGACY_THEME_PREFERENCES.custom.mode, "dark");
  assert.equal(LEGACY_THEME_PREFERENCES["custom-light"].mode, "light");
});
