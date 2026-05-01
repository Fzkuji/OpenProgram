import React from 'react';
import { render } from './runtime/index';
import { REPL } from './screens/REPL.js';
import { Demo } from './screens/Demo.js';
import { BackendClient } from './ws/client.js';
import { ThemeProvider } from './theme/ThemeProvider.js';
import { detectAutoTheme } from './theme/autoTheme.js';
import { setCachedSystemTheme } from './theme/systemTheme.js';

function parseArgs(argv: string[]): { ws: string; demo: boolean } {
  let ws = process.env.OPENPROGRAM_WS ?? 'ws://127.0.0.1:8765/ws';
  let demo = false;
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--ws' && argv[i + 1]) {
      ws = argv[i + 1]!;
      i++;
    }
    if (argv[i] === '--demo') {
      demo = true;
    }
  }
  return { ws, demo };
}

const { ws, demo } = parseArgs(process.argv.slice(2));
const client = new BackendClient(ws);
if (!demo) client.connect();

// Initial auto-theme detection. ThemeProvider keeps auto refreshed during
// runtime when the terminal is focused.
detectAutoTheme(null)
  .then((bg) => { if (bg) setCachedSystemTheme(bg); })
  .catch(() => { /* fall back to COLORFGBG / dark */ });

process.on('SIGINT', () => process.exit(0));
process.on('SIGTERM', () => process.exit(0));

async function main(): Promise<void> {
  if (!demo && process.stdout.isTTY) {
    process.stdout.write('\x1b[2J\x1b[3J\x1b[H');
  }
  const root = demo
    ? <ThemeProvider><Demo /></ThemeProvider>
    : <ThemeProvider><REPL client={client} /></ThemeProvider>;
  const instance = await render(root, { exitOnCtrlC: false });
  await instance.waitUntilExit();
  client.close();
  process.exit(0);
}

main().catch((err: unknown) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
