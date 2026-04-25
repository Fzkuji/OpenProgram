import { BackendClient } from '../ws/client.js';
import { SLASH_COMMANDS } from './registry.js';

export interface SlashContext {
  client: BackendClient;
  /** Append a system-style note (gray, no role label). */
  pushSystem: (text: string) => void;
  clearCommitted: () => void;
  newSession: () => void;
  exit: () => void;
  /** Open an interactive picker (model / resume / agent). */
  openPicker: (kind: 'model' | 'resume' | 'agent') => void;
  /** Toggle (or set) the "tools-on" flag passed with the next chat turn. */
  toggleTools: () => void;
  /** Export the current transcript to a markdown file. */
  exportTranscript: (filename?: string) => string;
  /** Get the most recent assistant reply text (for /copy). */
  lastAssistantText?: () => string | null;
  /** Copy the given text to the system clipboard. */
  copyToClipboard?: (text: string) => Promise<boolean>;
  currentAgent?: string;
  currentModel?: string;
  currentConversation?: string;
}

const helpText = (): string => {
  const lines = ['Available commands:'];
  for (const c of SLASH_COMMANDS) {
    lines.push(`  /${c.name.padEnd(14)} ${c.description}`);
  }
  return lines.join('\n');
};

const attachUsage = (
  'Usage: /attach <channel> <account> <peer>\n' +
  '  channel : wechat | telegram | discord | slack\n' +
  '  account : the account_id you registered (e.g. "default", "work")\n' +
  '  peer    : the channel-side user/chat id (wxid_xxx, chat_id, …)\n' +
  '\n' +
  'After attach, that peer\'s inbound messages route into the current\n' +
  'session instead of the agent.session_scope default.'
);

const detachUsage = (
  'Usage: /detach <channel> <account> <peer>'
);

const tokenize = (s: string): string[] =>
  s.trim().split(/\s+/).filter((x) => x.length > 0);

/**
 * Try to handle a slash line in-process. Returns true when the command was
 * recognized (caller should NOT forward it to the LLM); false to forward as
 * a plain chat message.
 */
export function handleSlash(line: string, ctx: SlashContext): boolean {
  const tokens = tokenize(line);
  if (tokens.length === 0 || !tokens[0]?.startsWith('/')) return false;
  const cmd = tokens[0]!.slice(1).toLowerCase();
  const args = tokens.slice(1);

  switch (cmd) {
    case 'help':
      // slash commands run silently — no user echo
      ctx.pushSystem(helpText());
      return true;

    case 'clear':
      ctx.clearCommitted();
      return true;

    case 'quit':
    case 'exit':
      ctx.exit();
      return true;

    case 'new': {
      ctx.newSession();
      ctx.pushSystem('Started a new session.');
      return true;
    }

    case 'session': {
      const lines = [
        `agent          : ${ctx.currentAgent ?? '—'}`,
        `model          : ${ctx.currentModel ?? '—'}`,
        `conversation   : ${ctx.currentConversation ?? '(new)'}`,
      ];
      // slash commands run silently — no user echo
      ctx.pushSystem(lines.join('\n'));
      return true;
    }

    case 'agents': {
      // slash commands run silently — no user echo
      ctx.client.send({ action: 'list_agents' });
      ctx.pushSystem('Listing agents… (see sidebar update once received)');
      return true;
    }

    case 'connections': {
      // slash commands run silently — no user echo
      ctx.client.send({ action: 'list_channel_bindings' });
      ctx.pushSystem('Listing channel bindings…');
      return true;
    }

    case 'aliases':
    case 'sessions': {
      // slash commands run silently — no user echo
      ctx.client.send({
        action: cmd === 'aliases' ? 'list_session_aliases' : 'list_conversations',
      });
      ctx.pushSystem(`Requested ${cmd}.`);
      return true;
    }

    case 'attach': {
      // slash commands run silently — no user echo
      if (args.length < 3) {
        ctx.pushSystem(attachUsage);
        return true;
      }
      const [channel, account_id, peer] = args as [string, string, string];
      if (!ctx.currentConversation) {
        ctx.pushSystem('No current conversation. Send a message first to create one.');
        return true;
      }
      ctx.client.send({
        action: 'attach_session',
        channel,
        account_id,
        peer,
        conversation_id: ctx.currentConversation,
      });
      ctx.pushSystem(
        `Attached ${channel}:${account_id}:${peer} → ${ctx.currentConversation}`,
      );
      return true;
    }

    case 'detach': {
      // slash commands run silently — no user echo
      if (args.length < 3) {
        ctx.pushSystem(detachUsage);
        return true;
      }
      const [channel, account_id, peer] = args as [string, string, string];
      ctx.client.send({ action: 'detach_session', channel, account_id, peer });
      ctx.pushSystem(`Detached ${channel}:${account_id}:${peer}`);
      return true;
    }

    case 'agent': {
      // /agent with no arg → picker; /agent <id> → direct switch.
      if (args.length < 1) {
        ctx.openPicker('agent');
        return true;
      }
      const id = args[0]!;
      ctx.client.send({ action: 'set_default_agent', id });
      ctx.pushSystem(`Set default agent → ${id}`);
      return true;
    }

    case 'model': {
      // /model with no arg → picker; /model <id> → direct switch.
      if (args.length < 1) {
        ctx.client.send({ action: 'list_models' });
        ctx.openPicker('model');
        return true;
      }
      ctx.client.send({ action: 'switch_model', model: args[0]!, conv_id: ctx.currentConversation });
      return true;
    }

    case 'resume': {
      ctx.openPicker('resume');
      return true;
    }

    case 'search': {
      // Same picker as /resume — Picker has a built-in filter, so user can
      // type to narrow titles. Different name on purpose so it shows up
      // distinctly in the slash menu.
      ctx.openPicker('resume');
      return true;
    }

    case 'tools': {
      ctx.toggleTools();
      return true;
    }

    case 'export': {
      const filename = args[0];
      try {
        const path = ctx.exportTranscript(filename);
        ctx.pushSystem(`Exported transcript → ${path}`);
      } catch (e) {
        ctx.pushSystem(`Export failed: ${(e as Error).message}`);
      }
      return true;
    }

    case 'cost': {
      // Token + cost stats live in the BottomBar; surface a snapshot here.
      ctx.client.send({ action: 'sync', conv_id: ctx.currentConversation } as never);
      ctx.pushSystem(
        'Current token usage is shown on the bottom bar. ↓ input, ↑ output.',
      );
      return true;
    }

    case 'web': {
      // Try to open the local web UI in the browser. Falls back to printing
      // the URL if the open package isn't available.
      try {
        const wsUrl = process.env.OPENPROGRAM_WS ?? '';
        const m = wsUrl.match(/^ws:\/\/(?:[^/]+):(\d+)/);
        if (m) {
          const port = m[1];
          const httpUrl = `http://localhost:${port}`;
          import('child_process').then(({ spawn }) => {
            const opener =
              process.platform === 'darwin' ? 'open'
              : process.platform === 'win32' ? 'start' : 'xdg-open';
            try {
              spawn(opener, [httpUrl], { stdio: 'ignore', detached: true }).unref();
            } catch {
              // ignore
            }
          });
          ctx.pushSystem(`Web UI: ${httpUrl}`);
        } else {
          ctx.pushSystem('Could not determine web UI URL from OPENPROGRAM_WS.');
        }
      } catch (e) {
        ctx.pushSystem(`/web failed: ${(e as Error).message}`);
      }
      return true;
    }

    case 'init': {
      try {
        const cwd = process.cwd();
        import('fs').then(({ writeFileSync, existsSync }) => {
          const seeds: Array<[string, string]> = [
            [
              'AGENTS.md',
              '# Agents\n\nDescribe agent personas in this directory: name, role, what they should know.\n',
            ],
            [
              'SOUL.md',
              '# Soul\n\nThe project\'s mission, voice, and guardrails go here.\n',
            ],
            [
              'USER.md',
              '# User profile\n\nWho the user is, how they communicate, what to remember.\n',
            ],
          ];
          for (const [name, content] of seeds) {
            const p = `${cwd}/${name}`;
            if (!existsSync(p)) writeFileSync(p, content);
          }
          ctx.pushSystem(
            `Initialized OpenProgram workspace at ${cwd}: AGENTS.md, SOUL.md, USER.md`,
          );
        });
      } catch (e) {
        ctx.pushSystem(`/init failed: ${(e as Error).message}`);
      }
      return true;
    }

    case 'login': {
      const channel = args[0];
      if (channel === 'wechat') {
        ctx.pushSystem(
          'WeChat login (QR scan via your phone):\n' +
          '  1. In another terminal, run:\n' +
          '       openprogram channels accounts login wechat default\n' +
          '  2. Scan the printed QR with your phone\'s WeChat app.\n' +
          '  3. The channel worker auto-starts after login. Incoming\n' +
          '     messages from your contacts will route to the default\n' +
          '     agent (or per /attach binding).\n' +
          '  4. Bind a specific contact to this session with:\n' +
          '       /attach wechat default <wxid>',
        );
        return true;
      }
      if (channel === 'telegram' || channel === 'discord' || channel === 'slack') {
        ctx.pushSystem(
          `${channel} login uses a bot token. In another terminal, run:\n` +
          `  openprogram channels accounts add ${channel} default\n` +
          'and paste the token when prompted.\n' +
          'Then use /attach ' + channel + ' default <peer_id> to route a peer here.',
        );
        return true;
      }
      ctx.pushSystem(
        'Channel login: /login <wechat|telegram|discord|slack>.\n' +
        'For provider auth (Anthropic / Codex / Gemini): run\n' +
        '  openprogram providers login <name> from the shell.',
      );
      return true;
    }

    case 'memory':
    case 'mcp':
    case 'doctor':
    case 'logout':
    case 'config':
    case 'review':
    case 'compact': {
      // Stubs — real implementations live behind ws actions that aren't
      // wired yet. Print a hint so the input doesn't fall through to the LLM.
      ctx.pushSystem(`/${cmd} is not implemented in the TUI yet — try \`openprogram ${cmd}\` from the shell.`);
      return true;
    }

    case 'copy': {
      const text = ctx.lastAssistantText?.();
      if (!text) {
        ctx.pushSystem('Nothing to copy yet.');
        return true;
      }
      ctx.copyToClipboard?.(text)
        .then((ok) => {
          ctx.pushSystem(ok ? 'Copied last assistant reply to clipboard.' : 'Clipboard backend not found.');
        })
        .catch((e) => {
          ctx.pushSystem(`Copy failed: ${(e as Error).message}`);
        });
      return true;
    }

    default:
      // Unknown slash command: treat as chat. Server may reject or the LLM
      // may handle it. We still forward so the user can see what happened.
      return false;
  }
}
