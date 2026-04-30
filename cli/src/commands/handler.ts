import { BackendClient } from '../ws/client.js';
import { SLASH_COMMANDS } from './registry.js';

type ThinkingEffort = 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';

export interface SlashContext {
  client: BackendClient;
  /** Append a system-style note (gray, no role label). */
  pushSystem: (text: string) => void;
  clearCommitted: () => void;
  newSession: () => void;
  exit: () => void;
  /** Open an interactive picker (model / resume / agent / channel / theme). */
  openPicker: (kind: 'model' | 'resume' | 'agent' | 'channel' | 'theme') => void;
  /** Apply a theme by name. Returns true on success, false on unknown name. */
  setTheme?: (name: string) => boolean;
  /** Toggle (or set) the "tools-on" flag passed with the next chat turn. */
  toggleTools: () => void;
  /** Current thinking budget shown on the bottom bar and sent with chat turns. */
  currentThinkingEffort?: ThinkingEffort;
  /** Set the thinking budget for subsequent chat turns. */
  setThinkingEffort?: (effort: ThinkingEffort) => void;
  /** Toggle the terminal-bell-on-long-turn-complete flag. */
  toggleBell: () => boolean;
  /** Re-show the Welcome banner as a system note. */
  showWelcome: () => void;
  /** Print details for the current agent. */
  showAgentInfo: () => void;
  /** Export the current transcript to a markdown file. */
  exportTranscript: (filename?: string) => string;
  /** Get the most recent assistant reply text (for /copy). */
  lastAssistantText?: () => string | null;
  /** Copy the given text to the system clipboard. */
  copyToClipboard?: (text: string) => Promise<boolean>;
  currentAgent?: string;
  currentModel?: string;
  currentConversation?: string;
  /**
   * Tell the REPL that the *next* ``session_aliases`` envelope should
   * be printed to the system area. Used by /aliases — picker
   * pre-fetches stay silent, so opening /channel doesn't dump a long
   * alias list into the transcript.
   */
  requestAliasesPrint?: () => void;
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
const ALIASES: Record<string, string> = {
  q: 'quit',
  h: 'help',
  n: 'new',
  m: 'model',
  r: 'resume',
  e: 'export',
  s: 'session',
  t: 'tools',
  c: 'clear',
  w: 'welcome',
};

const THINKING_EFFORTS: ThinkingEffort[] = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'];

const normalizeThinkingEffort = (raw: string): ThinkingEffort | null => {
  const v = raw.toLowerCase();
  if (v === 'min') return 'minimal';
  if (v === 'med') return 'medium';
  if (v === 'xhi' || v === 'x-high' || v === 'extra-high') return 'xhigh';
  if ((THINKING_EFFORTS as string[]).includes(v)) return v as ThinkingEffort;
  return null;
};

export function handleSlash(line: string, ctx: SlashContext): boolean {
  const tokens = tokenize(line);
  if (tokens.length === 0 || !tokens[0]?.startsWith('/')) return false;
  const raw = tokens[0]!.slice(1).toLowerCase();
  const cmd = ALIASES[raw] ?? raw;
  const args = tokens.slice(1);

  switch (cmd) {
    case 'help':
      // slash commands run silently — no user echo
      ctx.pushSystem(helpText());
      return true;

    case 'clear':
      // Clearing only resets React state — Ink's <Static> already-printed
      // turns stay on the terminal scrollback. Type / for /welcome to
      // re-print the banner.
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
      if (cmd === 'aliases') {
        ctx.requestAliasesPrint?.();
      }
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
      // Server lazy-creates the SessionDB row when missing — see
      // attach_session WS handler. So if currentConversation is
      // unset, we send empty and the UI updates once a chat lands.
      // Honest messaging without forcing a dummy turn first.
      ctx.client.send({
        action: 'attach_session',
        channel,
        account_id,
        peer,
        session_id: ctx.currentConversation ?? '',
        peer_kind: 'direct',
        peer_id: peer,
      });
      ctx.pushSystem(
        ctx.currentConversation
          ? `Attached ${channel}:${account_id}:${peer} → ${ctx.currentConversation}`
          : `Attached ${channel}:${account_id}:${peer}. Open a chat or wait for ` +
            `inbound — the session will materialize on first message.`,
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
      // /agent with no arg → picker; /agent inspect → details; /agent <id> → switch.
      if (args.length < 1) {
        ctx.openPicker('agent');
        return true;
      }
      if (args[0] === 'inspect' || args[0] === 'info' || args[0] === 'show') {
        ctx.showAgentInfo();
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

    case 'effort': {
      if (!ctx.setThinkingEffort) {
        ctx.pushSystem('/effort is not available in this screen.');
        return true;
      }
      if (args.length < 1) {
        ctx.pushSystem(
          `Thinking effort: ${ctx.currentThinkingEffort ?? 'xhigh'}\n` +
          `Usage: /effort <${THINKING_EFFORTS.join('|')}>`,
        );
        return true;
      }
      const effort = normalizeThinkingEffort(args[0]!);
      if (!effort) {
        ctx.pushSystem(`Unknown effort '${args[0]}'. Use one of: ${THINKING_EFFORTS.join(', ')}`);
        return true;
      }
      ctx.setThinkingEffort(effort);
      ctx.pushSystem(`Thinking effort set to ${effort}.`);
      return true;
    }

    case 'resume': {
      // Refresh the conversation list before opening the picker — the
      // server's list_conversations action returns BOTH in-memory webui
      // sessions AND on-disk per-agent sessions (where channel-bound
      // chats live). Without this refresh the picker only shows the
      // history_list snapshot from connect time, which omits any
      // wechat / telegram sessions started by the channels worker.
      ctx.client.send({ action: 'list_conversations' });
      ctx.openPicker('resume');
      return true;
    }

    case 'search': {
      // Two modes:
      //   /search                 → falls back to the resume picker (title
      //                              filter only — kept for muscle-memory)
      //   /search <query…>        → SessionDB FTS5 across every session's
      //                              messages; results land via WS as a
      //                              ``search_results`` envelope and the
      //                              picker is opened with those rows
      const query = args.join(' ').trim();
      if (!query) {
        ctx.client.send({ action: 'list_conversations' });
        ctx.openPicker('resume');
        return true;
      }
      ctx.client.send({
        action: 'search_messages',
        query,
        limit: 50,
      } as never);
      ctx.pushSystem(`Searching for "${query}"…`);
      // Picker opens when the search_results envelope arrives — see
      // ws/client.ts handler for that frame type.
      return true;
    }

    case 'tools': {
      ctx.toggleTools();
      return true;
    }

    case 'channel': {
      // Multi-step: pick channel → pick account → guides /attach.
      ctx.client.send({ action: 'list_channel_accounts' });
      ctx.openPicker('channel');
      return true;
    }

    case 'browser': {
      // Drive the attached Chrome from inside the TUI:
      //   /browser                       → status
      //   /browser <url>                 → open(url) (auto-bootstrap if needed)
      //   /browser <verb> <args…>        → arbitrary tool call (advanced)
      // Result is rendered as a system note when browser_result arrives.
      const sub = args[0] ?? '';
      const looksLikeUrl =
        /^https?:\/\//i.test(sub) || sub.startsWith('localhost') || sub.includes('.');
      if (!sub) {
        ctx.client.send({
          action: 'browser', verb: 'list', args: {},
        } as never);
        ctx.pushSystem('Asking the server for current browser sessions…');
        return true;
      }
      if (looksLikeUrl) {
        const url = sub.startsWith('http') ? sub : `https://${sub}`;
        ctx.client.send({
          action: 'browser', verb: 'open', args: { url },
        } as never);
        ctx.pushSystem(`Opening ${url} in attached Chrome…`);
        return true;
      }
      // Treat as <verb> + key=value pairs.
      const verb = sub;
      const kvArgs: Record<string, string> = {};
      for (const a of args.slice(1)) {
        const eq = a.indexOf('=');
        if (eq > 0) kvArgs[a.slice(0, eq)] = a.slice(eq + 1);
      }
      ctx.client.send({
        action: 'browser', verb, args: kvArgs,
      } as never);
      ctx.pushSystem(`browser ${verb}…`);
      return true;
    }

    case 'bell': {
      const on = ctx.toggleBell();
      ctx.pushSystem(`Terminal bell on long turns: ${on ? 'on' : 'off'}`);
      return true;
    }

    case 'theme': {
      // /theme            → picker
      // /theme <name>     → direct switch (dark / dark-dim / light / light-dim)
      if (args.length < 1) {
        ctx.openPicker('theme');
        return true;
      }
      const name = args[0]!;
      const ok = ctx.setTheme?.(name) ?? false;
      ctx.pushSystem(
        ok
          ? `Theme set to ${name}.`
          : `Unknown theme '${name}'. Try /theme to pick from a list.`,
      );
      return true;
    }

    case 'welcome': {
      ctx.showWelcome();
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

    case 'diff': {
      // Show the working-tree diff. Spawn git, capture stdout, render as
      // a system note. Bounded — too long renders a (+N more) tail.
      try {
        const range = args.join(' ') || '';
        import('child_process').then(({ spawnSync }) => {
          const out = spawnSync('git', range ? ['diff', range] : ['diff'], {
            encoding: 'utf8',
            maxBuffer: 1024 * 1024,
          });
          if (out.status !== 0 && (out.stderr ?? '').trim()) {
            ctx.pushSystem(`git diff: ${out.stderr}`);
            return;
          }
          const text = (out.stdout ?? '').trimEnd();
          if (!text) {
            ctx.pushSystem('No working-tree changes.');
            return;
          }
          const lines = text.split('\n');
          const cap = 60;
          const shown = lines.slice(0, cap).join('\n');
          const tail = lines.length > cap ? `\n… (+${lines.length - cap} more lines)` : '';
          ctx.pushSystem(`${shown}${tail}`);
        });
      } catch (e) {
        ctx.pushSystem(`/diff failed: ${(e as Error).message}`);
      }
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
