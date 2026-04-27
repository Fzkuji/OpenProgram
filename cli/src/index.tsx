import React from 'react';
import { render } from 'ink';
import { REPL } from './screens/REPL.js';
import { BackendClient } from './ws/client.js';
import { ThemeProvider } from './theme/ThemeProvider.js';
import { queryTerminalBg } from './theme/oscQuery.js';
import { setCachedSystemTheme } from './theme/systemTheme.js';

function parseArgs(argv: string[]): { ws: string } {
  let ws = process.env.OPENPROGRAM_WS ?? 'ws://127.0.0.1:8765/ws';
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--ws' && argv[i + 1]) {
      ws = argv[i + 1]!;
      i++;
    }
  }
  return { ws };
}

const { ws } = parseArgs(process.argv.slice(2));
const client = new BackendClient(ws);
client.connect();

// Startup screen reset. We intentionally do NOT use altscreen
// (\e[?1049h) — Apple's Terminal preserves the pre-switch cursor
// position across the switch and Ink ends up rendering its first frame
// from a row that isn't (1,1), leaving the top of the screen blank.
// Different versions of Terminal/iTerm2 also merge primary + altscreen
// scrollback in surprising ways. Stay in the primary buffer and just:
//   \e[H    cursor home
//   \e[2J   erase the visible viewport
//   \e[3J   erase scrollback (xterm extension; Terminal.app, iTerm2,
//           VSCode, Ghostty, kitty, GNOME Terminal, Windows Terminal)
// After this the terminal is genuinely empty, cursor at (1,1), and
// Ink can render top-down from there. The trade-off vs. altscreen:
// when openprogram exits, the chat content stays on screen instead
// of being replaced by the original shell view — but the launch
// experience is reliable across emulators.
process.stdout.write('\x1b[H\x1b[2J\x1b[3J');

// OSC 11 (background-color query) for the auto theme. The reply lands
// via setCachedSystemTheme whenever it arrives; ThemeProvider's
// subscriber bumps state and flips 'auto' from dark to light in place.
queryTerminalBg(200)
  .then((bg) => { if (bg) setCachedSystemTheme(bg); })
  .catch(() => { /* fall back to COLORFGBG / dark */ });

// Resize is left to Ink. Its own listener recomputes Yoga layout and
// triggers a re-render — adding our own clear-screen on top of that
// raced against log-update's previousLineCount and produced blank
// frames at certain widths.

process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));

const { waitUntilExit } = render(
  <ThemeProvider>
    <REPL client={client} />
  </ThemeProvider>,
  { exitOnCtrlC: false },
);

waitUntilExit().then(() => {
  client.close();
  process.exit(0);
});
