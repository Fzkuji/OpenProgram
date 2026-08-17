/**
 * Legacy single-theme export. New code should use `useColors()` from
 * ./ThemeProvider so palettes can switch at runtime via /theme.
 *
 * Kept as the dark palette so any non-React caller (e.g. tests imported
 * outside a provider) gets a sensible default.
 */
export { getTheme as getColors, THEME_NAMES, THEME_SETTINGS, THEME_LABELS, isThemeName, isThemeSetting } from './themes.js';
export type { ColorTheme, ThemeName, ThemeSetting } from './themes.js';
import { getTheme } from './themes.js';

export const colors = getTheme('dark');
