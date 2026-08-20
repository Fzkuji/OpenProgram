const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const themeChrome = require("../theme-chrome");

function checkBackgroundMapUsesRealHexes() {
  assert.equal(themeChrome.backgroundForTheme("beige-dark"), "#262624");
  assert.equal(themeChrome.backgroundForTheme("beige-light"), "#faf9f5");
  assert.equal(themeChrome.backgroundForTheme("dark"), "#1e1e20");
  assert.equal(themeChrome.backgroundForTheme("light"), "#ffffff");
  assert.equal(themeChrome.backgroundForTheme("aurora"), "#171528");
  assert.equal(themeChrome.backgroundForTheme("aurora-light"), "#fbfaff");
  assert.equal(themeChrome.backgroundForTheme("custom"), "#262624");
  assert.equal(themeChrome.backgroundForTheme("custom-light"), "#faf9f5");
  assert.notEqual(themeChrome.backgroundForTheme("beige-light"), "#141416");
  assert.notEqual(themeChrome.backgroundForTheme("beige-dark"), "#141416");
}

function checkStoredCustomMigratesToBeige() {
  assert.deepEqual(
    themeChrome.resolveThemePreference("custom", "dark", true),
    { theme: "beige-dark", resolvedMode: "dark" },
  );
  assert.deepEqual(
    themeChrome.resolveThemePreference("custom", "light", false),
    { theme: "beige-light", resolvedMode: "light" },
  );
  assert.equal(themeChrome.resolveThemePreference("custom", "auto", false).theme, "beige-light");
  assert.equal(themeChrome.resolveThemePreference("custom", "auto", true).theme, "beige-dark");
}

function checkSchema3PrefsBeatLegacy() {
  const light = themeChrome.resolveFromPrefBag({
    schema: "3",
    style: "beige",
    mode: "light",
    legacy: "beige-dark",
  }, true);
  assert.equal(light.theme, "beige-light");
  assert.equal(light.backgroundColor, "#faf9f5");

  const customLight = themeChrome.resolveFromPrefBag({
    schema: "3",
    style: "custom",
    mode: "light",
    legacy: "custom",
  }, true);
  assert.equal(customLight.style, "beige");
  assert.equal(customLight.theme, "beige-light");
  assert.equal(customLight.backgroundColor, "#faf9f5");

  const customDark = themeChrome.resolveFromPrefBag({
    schema: "3",
    style: "custom",
    mode: "dark",
    legacy: "custom",
  }, false);
  assert.equal(customDark.style, "beige");
  assert.equal(customDark.theme, "beige-dark");
  assert.equal(customDark.backgroundColor, "#262624");
}

function checkLegacyMigration() {
  assert.deepEqual(themeChrome.legacyThemePreference("custom", "2"), {
    style: "beige",
    mode: "dark",
  });
  assert.deepEqual(themeChrome.legacyThemePreference("custom-light", "2"), {
    style: "beige",
    mode: "light",
  });
  assert.deepEqual(themeChrome.legacyThemePreference("light", "1"), {
    style: "beige",
    mode: "light",
  });
  assert.deepEqual(themeChrome.legacyThemePreference("light", "2"), {
    style: "neutral",
    mode: "light",
  });
}

function checkPrefsFileRoundTrip() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "op-theme-chrome-"));
  try {
    const filePath = path.join(dir, themeChrome.PREFS_FILE_NAME);
    themeChrome.writePrefsFile(filePath, {
      style: "beige",
      mode: "light",
      theme: "beige-light",
    });
    const resolved = themeChrome.loadResolvedChrome({
      userDataPath: dir,
      systemDark: true,
      readChromium: () => ({}),
    });
    assert.equal(resolved.theme, "beige-light");
    assert.equal(resolved.backgroundColor, "#faf9f5");
    assert.equal(resolved.mode, "light");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function checkChromiumLocalStorageWinsOverCache() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "op-theme-ls-"));
  try {
    themeChrome.writePrefsFile(path.join(dir, themeChrome.PREFS_FILE_NAME), {
      style: "neutral",
      mode: "dark",
      theme: "dark",
    });
    const levelDir = path.join(dir, "Local Storage", "leveldb");
    fs.mkdirSync(levelDir, { recursive: true });
    fs.writeFileSync(
      path.join(levelDir, "000003.log"),
      Buffer.from("agentic_theme_style\x00beige\nagentic_theme_mode\x00light\nagentic_theme_schema\x003\n"),
    );
    const resolved = themeChrome.loadResolvedChrome({
      userDataPath: dir,
      systemDark: true,
    });
    assert.equal(resolved.style, "beige");
    assert.equal(resolved.mode, "light");
    assert.equal(resolved.theme, "beige-light");
    assert.equal(resolved.backgroundColor, "#faf9f5");
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function checkLegacyKeyDoesNotEatStyleSuffix() {
  const buffer = Buffer.from(
    "agentic_theme_style\x00beige\x00agentic_theme_mode\x00auto\x00agentic_theme\x00beige-dark",
  );
  assert.equal(
    themeChrome.extractPrefFromBuffer(buffer, "agentic_theme", [...themeChrome.THEME_IDS, "auto"]),
    "beige-dark",
  );
  assert.equal(
    themeChrome.extractPrefFromBuffer(buffer, "agentic_theme_style", themeChrome.THEME_STYLES),
    "beige",
  );
}

function checkErrorPageAndListingUseThemeSurface() {
  const chrome = themeChrome.chromeForTheme("beige-light");
  const html = themeChrome.buildErrorPageHtml(chrome, "openprogram worker start");
  assert.match(html, /background:#faf9f5/);
  assert.doesNotMatch(html, /#141416/);
  const url = themeChrome.buildErrorPageUrl(chrome, "openprogram worker start");
  assert.equal(themeChrome.isErrorPageUrl(url), true);
  assert.equal(themeChrome.isErrorPageUrl("http://127.0.0.1:18100/chat"), false);
  const css = themeChrome.directoryListingCss(chrome);
  assert.match(css, /background: #faf9f5/);
  assert.doesNotMatch(css, /prefers-color-scheme/);
  assert.doesNotMatch(css, /#141416/);
}

function checkAccentOverrideChangesLinkNotBackground() {
  const overridden = themeChrome.resolveFromPrefBag({
    schema: "3",
    style: "beige",
    mode: "dark",
    legacy: "beige-dark",
    accent: "#2563eb",
  }, true);
  assert.equal(overridden.backgroundColor, "#262624");
  assert.equal(overridden.chrome.bg, "#262624");
  assert.equal(overridden.chrome.link, "#2563eb");
  assert.equal(overridden.accentColor, "#2563eb");

  const defaults = themeChrome.resolveFromPrefBag({
    schema: "3",
    style: "beige",
    mode: "dark",
    legacy: "beige-dark",
  }, true);
  assert.equal(defaults.chrome.link, "#d97757");
  assert.equal(defaults.backgroundColor, "#262624");

  const listing = themeChrome.directoryListingCss(overridden.chrome);
  assert.match(listing, /color: #2563eb/);
  const error = themeChrome.buildErrorPageHtml(overridden.chrome, "openprogram worker start");
  assert.match(error, /background:#262624/);
}

function checkColorToHex() {
  assert.equal(themeChrome.colorToHex("#faf9f5"), "#faf9f5");
  assert.equal(themeChrome.colorToHex("#ABC"), "#aabbcc");
  assert.equal(themeChrome.colorToHex("rgb(250, 249, 245)"), "#faf9f5");
  assert.equal(themeChrome.colorToHex("not-a-color"), null);
}

checkBackgroundMapUsesRealHexes();
checkStoredCustomMigratesToBeige();
checkSchema3PrefsBeatLegacy();
checkLegacyMigration();
checkPrefsFileRoundTrip();
checkChromiumLocalStorageWinsOverCache();
checkLegacyKeyDoesNotEatStyleSuffix();
checkErrorPageAndListingUseThemeSurface();
checkAccentOverrideChangesLinkNotBackground();
checkColorToHex();

console.log("theme chrome checks passed");
