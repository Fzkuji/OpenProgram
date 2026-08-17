/**
 * One-shot OSC 11 background-color query.
 *
 * Most terminals (iTerm2, Terminal.app, kitty, ghostty, Alacritty, xterm,
 * GNOME Terminal, Konsole, Windows Terminal) respond to `\x1b]11;?\x1b\\`
 * with their bg color via stdin in the form `\x1b]11;rgb:RRRR/GGGG/BBBB\x1b\\`.
 * We send the query at startup (before Ink takes over stdin), wait briefly
 * for the response, parse it, and choose a concrete palette by BT.709
 * luminance.
 *
 * If the terminal doesn't answer within the timeout (e.g. it's piped, or
 * doesn't support OSC 11), the promise resolves with undefined and the
 * caller falls back to $COLORFGBG / 'dark'.
 */

import type { ThemeName } from './themes.js';
import type { TerminalQuerier } from '../runtime/ink/terminal-querier.js';
import { oscColor } from '../runtime/ink/terminal-querier.js';

export type SystemTheme = ThemeName;

const ST = '\x1b\\'; // String Terminator
const OSC_BG_QUERY = `\x1b]11;?${ST}`;

export interface RGB { r: number; g: number; b: number }

/** Normalize a 1–4 digit hex component to [0, 1]. */
const hexComponent = (hex: string): number => {
  const max = 16 ** hex.length - 1;
  return parseInt(hex, 16) / max;
};

const parseOscRgb = (data: string): RGB | undefined => {
  // rgb:RRRR/GGGG/BBBB — each component is 1–4 hex digits.
  const m = /rgba?:([0-9a-f]{1,4})\/([0-9a-f]{1,4})\/([0-9a-f]{1,4})/i.exec(data);
  if (!m) return undefined;
  return {
    r: hexComponent(m[1]!),
    g: hexComponent(m[2]!),
    b: hexComponent(m[3]!),
  };
};

export const themeFromRgb = (rgb: RGB): SystemTheme => {
  // BT.709 relative luminance. Use dim palettes for non-extreme
  // backgrounds so auto does not over-saturate tinted or gray terminals.
  const luminance = 0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b;
  if (luminance < 0.18) return 'dark';
  if (luminance < 0.5) return 'dark-dim';
  if (luminance > 0.86) return 'light';
  return 'light-dim';
};

export const themeFromOscData = (data: string): SystemTheme | undefined => {
  const rgb = parseOscRgb(data);
  return rgb ? themeFromRgb(rgb) : undefined;
};

export async function queryTerminalBg(timeoutMs = 200): Promise<SystemTheme | undefined> {
  // Only meaningful on a TTY where stdin can be set to raw mode.
  if (!process.stdin.isTTY || !process.stdout.isTTY) return undefined;

  const stdin = process.stdin;
  let buf = '';

  return new Promise<SystemTheme | undefined>((resolve) => {
    let done = false;
    let timer: NodeJS.Timeout | undefined;
    // Cleanup leaves stdin's raw-mode + flowing state alone. Ink takes
    // over the same stdin moments later (synchronously after we return)
    // and manages those flags itself; if we toggle them on the way out
    // we'd race against Ink's setup and break user input.
    const cleanup = (result: SystemTheme | undefined) => {
      if (done) return;
      done = true;
      if (timer) clearTimeout(timer);
      stdin.removeListener('data', onData);
      resolve(result);
    };
    const onData = (chunk: Buffer) => {
      buf += chunk.toString('binary');
      // Look for the OSC 11 reply payload. Terminator may be BEL or ST.
      const match = /\x1b\]11;([^\x07\x1b]*)(?:\x07|\x1b\\)/.exec(buf);
      if (!match) return;
      const rgb = parseOscRgb(match[1] ?? '');
      cleanup(rgb ? themeFromRgb(rgb) : undefined);
    };

    try {
      // Raw mode is required so the terminal sends the OSC reply byte-for-
      // byte instead of waiting for a newline. We keep it raw afterwards —
      // Ink's renderer also wants raw mode, so this is a wash.
      stdin.setRawMode(true);
      stdin.resume();
      stdin.on('data', onData);
      process.stdout.write(OSC_BG_QUERY);
    } catch {
      cleanup(undefined);
      return;
    }
    timer = setTimeout(() => cleanup(undefined), timeoutMs);
  });
}

export async function queryTerminalBgWithQuerier(
  querier: TerminalQuerier | null | undefined,
  timeoutMs = 500,
): Promise<SystemTheme | undefined> {
  if (!querier) return undefined;
  const query = Promise.all([
    querier.send(oscColor(11)),
    querier.flush(),
  ]).then(([response]) => response);
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<undefined>((resolve) => {
    timer = setTimeout(() => resolve(undefined), timeoutMs);
  });
  const response = await Promise.race([query, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
  return response?.type === 'osc' ? themeFromOscData(response.data) : undefined;
}
