import React from 'react';
import { render } from 'ink';
import { REPL } from './screens/REPL.js';
import { BackendClient } from './ws/client.js';

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

// Enter alternate screen buffer so the TUI owns the whole terminal and
// anything printed before this (server logs, warnings) stays on the
// primary screen, hidden behind us.
process.stdout.write('\x1b[?1049h\x1b[2J\x1b[H');

const restoreScreen = () => {
  process.stdout.write('\x1b[?1049l');
};

process.on('exit', restoreScreen);
process.on('SIGINT', () => {
  restoreScreen();
  process.exit(0);
});
process.on('SIGTERM', () => {
  restoreScreen();
  process.exit(0);
});

const { waitUntilExit } = render(<REPL client={client} />, {
  exitOnCtrlC: false,
});

waitUntilExit().then(() => {
  client.close();
  restoreScreen();
  process.exit(0);
});
