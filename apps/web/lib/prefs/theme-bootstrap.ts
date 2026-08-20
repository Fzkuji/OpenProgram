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
function normalizeAccentHex(value) {
  if (typeof value !== 'string') return null;
  var match = value.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  if (match[1].length === 3) {
    return '#' + match[1].split('').map(function (ch) { return ch + ch; }).join('').toLowerCase();
  }
  return '#' + match[1].toLowerCase();
}
function deriveAccentHover(hex) {
  var normalized = normalizeAccentHex(hex);
  if (!normalized) return hex;
  function to(n) {
    return Math.max(0, Math.min(255, Math.round(n * 0.86))).toString(16).padStart(2, '0');
  }
  return '#' + to(parseInt(normalized.slice(1, 3), 16))
    + to(parseInt(normalized.slice(3, 5), 16))
    + to(parseInt(normalized.slice(5, 7), 16));
}
function rewriteCustomCssForOverlay(css) {
  return String(css)
    .replace(/\\[data-theme\\s*=\\s*(["']?)custom-light\\1\\]/g, '[data-custom-css="on"][data-theme-mode="light"]')
    .replace(/\\[data-theme\\s*=\\s*(["']?)custom\\1\\]/g, '[data-custom-css="on"][data-theme-mode="dark"]');
}
var priorSchema = themeStorageGet('agentic_theme_schema') || '1';
var old = themeStorageGet('agentic_theme') || 'auto';
var storedStyle = themeStorageGet('agentic_theme_style');
var wasCustom = storedStyle === 'custom' || old === 'custom' || old === 'custom-light';
var migrated = LEGACY[old] || LEGACY.auto;
if (priorSchema !== '2' && (old === 'dark' || old === 'light')) {
  migrated = { style: 'beige', mode: old };
}
if (wasCustom) migrated = { style: 'beige', mode: migrated.mode || 'dark' };
if (!STYLES.includes(themeStorageGet('agentic_theme_style'))) {
  themeStorageSet('agentic_theme_style', migrated.style);
}
if (!MODES.includes(themeStorageGet('agentic_theme_mode'))) {
  themeStorageSet('agentic_theme_mode', migrated.mode);
}
themeStorageSet('agentic_theme_schema', '3');
var savedCss = themeStorageGet('agentic_custom_css') || '';
var enabled = themeStorageGet('agentic_custom_css_enabled');
if (enabled !== '0' && enabled !== '1') {
  enabled = savedCss.trim() ? '1' : '0';
}
if (storedStyle === 'custom' && savedCss.trim()) enabled = '1';
themeStorageSet('agentic_custom_css_enabled', enabled);
function readStyle() {
  var value = themeStorageGet('agentic_theme_style');
  return STYLES.includes(value) ? value : 'beige';
}
function readMode() {
  var value = themeStorageGet('agentic_theme_mode');
  return MODES.includes(value) ? value : 'auto';
}
function readAccent() {
  return normalizeAccentHex(themeStorageGet('agentic_theme_accent') || '');
}
function applyAccentOverride() {
  var accent = readAccent();
  var root = document.documentElement.style;
  if (!accent) {
    root.removeProperty('--accent-orange');
    root.removeProperty('--accent-fill');
    root.removeProperty('--accent-orange-hover');
    return accent;
  }
  root.setProperty('--accent-orange', accent);
  root.setProperty('--accent-fill', accent);
  root.setProperty('--accent-orange-hover', deriveAccentHover(accent));
  return accent;
}
function applyOverlayCss() {
  var css = themeStorageGet('agentic_custom_css') || '';
  var on = themeStorageGet('agentic_custom_css_enabled') === '1' && css.trim();
  var el = document.getElementById('user-custom-css');
  if (!on) {
    if (el) el.remove();
    document.documentElement.removeAttribute('data-custom-css');
    return;
  }
  document.documentElement.setAttribute('data-custom-css', 'on');
  if (!el) {
    el = document.createElement('style');
    el.id = 'user-custom-css';
    document.head.appendChild(el);
  }
  el.textContent = rewriteCustomCssForOverlay(css);
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
  var accent = applyAccentOverride();
  applyOverlayCss();
  try {
    if (window.openprogramDesktop && window.openprogramDesktop.theme) {
      window.openprogramDesktop.theme.setChrome({
        theme: theme,
        style: style,
        mode: mode,
        accentColor: accent || undefined
      });
    }
  } catch (_) {}
}
applyThemePreference();
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
  if (readMode() === 'auto') applyThemePreference();
});
window.addEventListener('storage', function (event) {
  if (
    event.key === 'agentic_theme_style'
    || event.key === 'agentic_theme_mode'
    || event.key === 'agentic_theme_accent'
    || event.key === 'agentic_custom_css'
    || event.key === 'agentic_custom_css_enabled'
  ) {
    applyThemePreference();
  }
});
`;
