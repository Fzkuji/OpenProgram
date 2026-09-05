export interface TuiOptions {
  ws: string;
  demo: boolean;
  probe: boolean;
  altScreen: boolean;
  screenReader: boolean;
  initialAgent?: string;
  initialConversation?: string;
}

const envEnabled = (name: string): boolean =>
  /^(1|true|yes|on)$/i.test(process.env[name]?.trim() ?? '');

/** Parse the deliberately small option surface passed by the Python launcher. */
export function parseTuiOptions(argv: string[]): TuiOptions {
  let ws = process.env.OPENPROGRAM_WS ?? 'ws://127.0.0.1:18100/ws';
  let demo = false;
  let probe = false;
  let altScreen = !envEnabled('OPENPROGRAM_TUI_NO_ALT_SCREEN');
  let screenReader = envEnabled('OPENPROGRAM_TUI_SCREEN_READER');

  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--ws' && argv[i + 1]) {
      ws = argv[++i]!;
    } else if (arg === '--demo') {
      demo = true;
    } else if (arg === '--probe') {
      probe = true;
    } else if (arg === '--no-alt-screen') {
      altScreen = false;
    } else if (arg === '--screen-reader') {
      screenReader = true;
    }
  }

  // Screen readers need ordinary terminal scrollback and cannot use the
  // cursor-addressed alternate buffer reliably.
  if (screenReader) altScreen = false;
  return {
    ws,
    demo,
    probe,
    altScreen,
    screenReader,
    initialAgent: process.env.OPENPROGRAM_AGENT || undefined,
    initialConversation: process.env.OPENPROGRAM_CONV || undefined,
  };
}
