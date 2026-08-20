import {
  LEGACY_THEME_PREFERENCES,
  THEME_MODES,
  THEME_STYLES,
  THEME_STYLE_PAIRS,
} from "./theme-config";

/** Synchronous first-paint theme setup. Kept executable for behavior tests. */
export const THEME_BOOTSTRAP_SCRIPT = `
var STYLES = ${JSON.stringify(THEME_STYLES)};
var MODES = ${JSON.stringify(THEME_MODES)};
var PAIRS = ${JSON.stringify(THEME_STYLE_PAIRS)};
var LEGACY = ${JSON.stringify(LEGACY_THEME_PREFERENCES)};
function themeStorageGet(key) {
  try { return window.localStorage.getItem(key); } catch (_) { return null; }
}
function themeStorageSet(key, value) {
  try { window.localStorage.setItem(key, value); } catch (_) {}
}
var priorSchema = themeStorageGet('agentic_theme_schema') || '1';
var old = themeStorageGet('agentic_theme') || 'auto';
var migrated = LEGACY[old] || LEGACY.auto;
if (priorSchema !== '2' && (old === 'dark' || old === 'light')) {
  migrated = { style: 'beige', mode: old };
}
if (!STYLES.includes(themeStorageGet('agentic_theme_style'))) {
  themeStorageSet('agentic_theme_style', migrated.style);
}
if (!MODES.includes(themeStorageGet('agentic_theme_mode'))) {
  themeStorageSet('agentic_theme_mode', migrated.mode);
}
themeStorageSet('agentic_theme_schema', '3');
function readStyle() {
  var value = themeStorageGet('agentic_theme_style');
  return STYLES.includes(value) ? value : 'beige';
}
function readMode() {
  var value = themeStorageGet('agentic_theme_mode');
  return MODES.includes(value) ? value : 'auto';
}
function applyThemePreference() {
  var style = readStyle();
  var mode = readMode();
  var resolvedMode = mode === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : mode;
  var theme = PAIRS[style][resolvedMode];
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.setAttribute('data-theme-style', style);
  document.documentElement.setAttribute('data-theme-mode', resolvedMode);
  themeStorageSet('agentic_theme', theme);
  try {
    if (window.openprogramDesktop && window.openprogramDesktop.theme) {
      window.openprogramDesktop.theme.setChrome({ theme: theme, style: style, mode: mode });
    }
  } catch (_) {}
}
applyThemePreference();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
  if (readMode() === 'auto') applyThemePreference();
});
window.addEventListener('storage', function (event) {
  if (event.key === 'agentic_theme_style' || event.key === 'agentic_theme_mode') {
    applyThemePreference();
  }
});
`;
