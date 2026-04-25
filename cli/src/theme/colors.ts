/**
 * Semantic theme tokens used across the Ink TUI.
 *
 * Names describe role, not appearance — "user.bg" rather than "#222".
 * Swap the values here to reskin without touching component code.
 */
export const colors = {
  // Common roles --------------------------------------------------------
  primary: 'cyan',
  secondary: 'gray',
  success: 'green',
  warning: 'yellow',
  error: 'red',
  muted: 'gray',
  accent: 'magenta',
  text: 'white',
  border: 'gray',

  // Chat-turn roles -----------------------------------------------------
  user: {
    /** Hex used as the user-message block background. Subtle so the
     * default white text on dark terminals stays readable. */
    bg: '#222',
    fg: 'white',
    glyph: 'cyan',
  },
  assistant: {
    bg: undefined as string | undefined,
    fg: 'white',
    glyph: 'green',
  },
  system: {
    bg: undefined as string | undefined,
    fg: 'gray',
    glyph: 'gray',
  },

  // Tool-call rendering -------------------------------------------------
  tool: {
    running: 'yellow',
    done: 'gray',
    error: 'red',
  },
} as const;

export type ColorTheme = typeof colors;
