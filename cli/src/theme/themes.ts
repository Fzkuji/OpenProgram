/**
 * Semantic theme tokens used across the Ink TUI.
 *
 * Same shape as the original `colors` const so component code keeps working
 * by simply replacing `import { colors }` with `const colors = useColors()`.
 *
 * Design notes:
 *   - `assistant.bg` and `system.bg` stay `undefined` so the terminal's own
 *     bg shines through. Only the user-message block paints a bg, mirroring
 *     Claude Code's chat layout.
 *   - Light variants use dark `text` so foreground stays readable when the
 *     terminal background is a bright color (the common daytime case).
 *   - The `*-dim` variants drop saturation; helpful in low-contrast terminals
 *     or when the high-saturation orange feels loud.
 */

import type { Color } from '../runtime/index';

export interface ColorTheme {
  // Common roles
  primary: Color;
  secondary: Color;
  success: Color;
  warning: Color;
  error: Color;
  muted: Color;
  accent: Color;
  text: Color;
  border: Color;

  // Chat-turn roles
  user: { bg: Color | undefined; fg: Color; glyph: Color };
  assistant: { bg: Color | undefined; fg: Color; glyph: Color };
  system: { bg: Color | undefined; fg: Color; glyph: Color };

  // Tool-call rendering
  tool: { running: Color; done: Color; error: Color };
}

export const THEME_NAMES = ['dark', 'dark-dim', 'light', 'light-dim'] as const;
/** A renderable palette. Always resolvable. */
export type ThemeName = (typeof THEME_NAMES)[number];

/**
 * What the user can save as a setting. `auto` is resolved at runtime to
 * one of the concrete ThemeName values via `getSystemThemeName()`.
 */
export const THEME_SETTINGS = ['auto', ...THEME_NAMES] as const;
export type ThemeSetting = (typeof THEME_SETTINGS)[number];

export const THEME_LABELS: Record<ThemeSetting, string> = {
  auto: 'Auto — match the terminal background',
  dark: 'Dark — warm orange-red on black',
  'dark-dim': 'Dark dim — softer accents, less eye strain at night',
  light: 'Light — black text, orange accent (daytime)',
  'light-dim': 'Light dim — muted accents on a light terminal',
};

const dark: ColorTheme = {
  primary: '#ff7a45',
  secondary: '#888888',
  success: '#7ad17a',
  warning: '#ffb86c',
  error: '#e25c4d',
  muted: '#9c9c9c',
  accent: '#d76b3a',
  text: '#f0f0f0',
  border: '#5a5a5a',
  user: { bg: '#3a2118', fg: '#f5e8da', glyph: '#ff7a45' },
  assistant: { bg: undefined, fg: '#f0f0f0', glyph: '#7ad17a' },
  system: { bg: undefined, fg: '#9c9c9c', glyph: '#9c9c9c' },
  tool: { running: '#ffb86c', done: '#9c9c9c', error: '#e25c4d' },
};

const darkDim: ColorTheme = {
  primary: '#d68a5e',
  secondary: '#7a7a7a',
  success: '#7fb087',
  warning: '#d6a96d',
  error: '#c46e62',
  muted: '#8a8a8a',
  accent: '#b87a52',
  text: '#dcdcdc',
  border: '#4d4d4d',
  user: { bg: '#2c1d18', fg: '#e8d9c8', glyph: '#d68a5e' },
  assistant: { bg: undefined, fg: '#dcdcdc', glyph: '#7fb087' },
  system: { bg: undefined, fg: '#8a8a8a', glyph: '#8a8a8a' },
  tool: { running: '#d6a96d', done: '#8a8a8a', error: '#c46e62' },
};

const light: ColorTheme = {
  // Black text on whatever the terminal's bg is (likely white-ish).
  // Accents pick up the warm orange so the brand still reads, but at a
  // saturation that contrasts cleanly with a bright background.
  primary: '#c44a17',
  secondary: '#5a5a5a',
  success: '#2c7a36',
  warning: '#a46500',
  error: '#a8261b',
  muted: '#5e5e5e',
  accent: '#a13b13',
  text: '#1a1a1a',
  border: '#bdbdbd',
  // Light cream tint for the user block so it reads as a quoted region
  // without going darker than the page itself.
  user: { bg: '#f4e4d4', fg: '#3a1f12', glyph: '#c44a17' },
  assistant: { bg: undefined, fg: '#1a1a1a', glyph: '#2c7a36' },
  system: { bg: undefined, fg: '#5e5e5e', glyph: '#5e5e5e' },
  tool: { running: '#a46500', done: '#5e5e5e', error: '#a8261b' },
};

const lightDim: ColorTheme = {
  primary: '#9b4a2a',
  secondary: '#6e6e6e',
  success: '#436e48',
  warning: '#8a6420',
  error: '#8b3a32',
  muted: '#6e6e6e',
  accent: '#8a4a2a',
  text: '#262626',
  border: '#c8c8c8',
  user: { bg: '#ecdfd1', fg: '#3a2418', glyph: '#9b4a2a' },
  assistant: { bg: undefined, fg: '#262626', glyph: '#436e48' },
  system: { bg: undefined, fg: '#6e6e6e', glyph: '#6e6e6e' },
  tool: { running: '#8a6420', done: '#6e6e6e', error: '#8b3a32' },
};

export const THEMES: Record<ThemeName, ColorTheme> = {
  dark,
  'dark-dim': darkDim,
  light,
  'light-dim': lightDim,
};

export const DEFAULT_THEME: ThemeName = 'dark';
/**
 * First-launch default. `auto` means we ask the terminal what its bg is
 * via OSC 11 and resolve to dark or light. Falls back to dark if the
 * terminal doesn't reply, so the worst case is the same as before.
 */
export const DEFAULT_SETTING: ThemeSetting = 'auto';

export function getTheme(name: ThemeName): ColorTheme {
  return THEMES[name] ?? THEMES[DEFAULT_THEME];
}

export function isThemeName(s: string): s is ThemeName {
  return (THEME_NAMES as readonly string[]).includes(s);
}

export function isThemeSetting(s: string): s is ThemeSetting {
  return (THEME_SETTINGS as readonly string[]).includes(s);
}
