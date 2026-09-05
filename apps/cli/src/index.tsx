import React from 'react';
import { render } from './runtime/index';
import { REPL } from './screens/REPL.js';
import { Demo } from './screens/Demo.js';
import { BackendClient } from './ws/client.js';
import { ThemeProvider } from './theme/ThemeProvider.js';
import { parseTuiOptions } from './options.js';
import { createTuiReadyHandshake } from './startupHandshake.js';

const {
  ws,
  demo,
  probe,
  altScreen,
  screenReader,
  initialAgent,
  initialConversation,
} = parseTuiOptions(process.argv.slice(2));

// Raw-mode Ctrl-C is handled by the REPL. Signals delivered by a supervisor,
// ssh disconnect, or `kill` still need conventional exit codes; process.exit
// runs the runtime's synchronous signal-exit terminal cleanup first.
process.once('SIGINT', () => process.exit(130));
process.once('SIGTERM', () => process.exit(143));

async function main(): Promise<void> {
  if (probe) {
    process.stdout.write('OpenProgram Ink TUI ready\n');
    return;
  }

  if (!process.stdin.isTTY || !process.stdout.isTTY) {
    process.stderr.write(
      'OpenProgram TUI requires terminal stdin and stdout; use `openprogram --print` for pipelines.\n',
    );
    process.exitCode = 2;
    return;
  }

  const client = new BackendClient(ws);
  try {
    if (!demo) client.connect();

    // ThemeProvider performs terminal queries through Ink's shared input
    // parser. A separate pre-render OSC listener races raw-mode setup and can
    // consume the user's first keystrokes on fast Linux terminals.
    const root = demo
      ? <ThemeProvider><Demo /></ThemeProvider>
      : (
        <ThemeProvider>
          <REPL
            client={client}
            altScreen={altScreen}
            screenReader={screenReader}
            initialAgent={initialAgent}
            initialConversation={initialConversation}
          />
        </ThemeProvider>
      );
    const startup = createTuiReadyHandshake();
    const instance = await render(root, {
      exitOnCtrlC: false,
      onFrame: startup.onFrame,
    });
    startup.mounted();
    await instance.waitUntilExit();
  } finally {
    // A synchronous Ink mount/raw-mode failure must not leave the WebSocket
    // (or one of its reconnect timers) keeping the CLI process alive.
    if (!demo) client.close();
  }
}

main().catch((err: unknown) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});
