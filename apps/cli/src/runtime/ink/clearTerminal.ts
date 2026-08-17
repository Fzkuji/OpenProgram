/**
 * Cross-platform terminal clearing with scrollback support.
 * Detects modern terminals that support ESC[3J for clearing scrollback.
 */

import { csi, CURSOR_HOME, ERASE_SCREEN, ERASE_SCROLLBACK } from './termio/csi.js'

// HVP (Horizontal Vertical Position) - legacy Windows cursor home
const CURSOR_HOME_WINDOWS = csi(0, 'f')

function isWindowsTerminal(): boolean {
  return process.platform === 'win32' && !!process.env.WT_SESSION
}

function isMintty(): boolean {
  // mintty 3.1.5+ sets TERM_PROGRAM to 'mintty'
  if (process.env.TERM_PROGRAM === 'mintty') {
    return true
  }

  // GitBash/MSYS2/MINGW use mintty and set MSYSTEM
  if (process.platform === 'win32' && process.env.MSYSTEM) {
    return true
  }

  return false
}

function isModernWindowsTerminal(): boolean {
  // Windows Terminal sets WT_SESSION environment variable
  if (isWindowsTerminal()) {
    return true
  }

  // VS Code integrated terminal on Windows with ConPTY support
  if (process.platform === 'win32' && process.env.TERM_PROGRAM === 'vscode' && process.env.TERM_PROGRAM_VERSION) {
    return true
  }

  // mintty (GitBash/MSYS2/Cygwin) supports modern escape sequences
  if (isMintty()) {
    return true
  }

  return false
}

/**
 * Returns the ANSI escape sequence to clear the terminal.
 *
 * eraseScrollback=true (default, alt-screen): erase visible cells AND
 * scrollback. Alt-screen has no scrollback semantically, so the 3J is
 * harmless there but ensures buffers are uniformly clean.
 *
 * eraseScrollback=false (main-screen inline-flow): erase visible cells
 * only — the user's scrollback (their shell history, plus any
 * emitToScrollback content from us) MUST survive resize / fullReset.
 * Without this guard, a window-drag wipes everything we wrote above
 * the live strip.
 */
export function getClearTerminalSequence(eraseScrollback = true): string {
  if (process.platform === 'win32') {
    if (isModernWindowsTerminal()) {
      return ERASE_SCREEN + (eraseScrollback ? ERASE_SCROLLBACK : '') + CURSOR_HOME
    } else {
      // Legacy Windows console - can't clear scrollback
      return ERASE_SCREEN + CURSOR_HOME_WINDOWS
    }
  }

  return ERASE_SCREEN + (eraseScrollback ? ERASE_SCROLLBACK : '') + CURSOR_HOME
}

/**
 * Clears the terminal screen. On supported terminals, also clears scrollback.
 */
export const clearTerminal = getClearTerminalSequence()
