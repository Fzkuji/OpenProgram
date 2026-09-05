import { BackendClient } from '../ws/client.js';
import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { PickerKind } from '../screens/repl/types.js';
import { allSlashCommands } from './registry.js';
import { backendBase, backendFetch, openInBrowser, webUiUrls } from '../utils/backend.js';

type ThinkingEffort = 'off' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';
type PermissionMode = 'ask' | 'acceptEdits' | 'plan' | 'auto' | 'bypass';

export interface SlashContext {
  client: BackendClient;
  /** Append a system-style note (gray, no role label). */
  pushSystem: (text: string) => void;
  clearCommitted: () => void;
  newSession: () => void;
  exit: () => void;
  /** Open an interactive picker. The kind union lives in
   *  screens/repl/types.ts — a second inline copy here drifted behind
   *  it once already (missing 'branch'). */
  openPicker: (kind: Exclude<PickerKind, null>) => void;
  /** Open the in-TUI Claude-account panel (claude-code provider): add /
   *  activate / deactivate / rename / remove, all without leaving the TUI. */
  openClaudeAccounts: () => void;
  /** Open the in-TUI account panel for ANY provider (same ops, generic over
   *  provider id) — the unified manager behind `/login <provider>`. */
  openProviderAccounts: (providerId: string) => void;
  /** Apply a theme by name. Returns true on success, false on unknown name. */
  setTheme?: (name: string) => boolean;
  /** Toggle (or set) the "tools-on" flag passed with the next chat turn. */
  toggleTools: () => void;
  /** Current thinking budget shown on the bottom bar and sent with chat turns. */
  currentThinkingEffort?: ThinkingEffort;
  /** Set the thinking budget for subsequent chat turns. */
  setThinkingEffort?: (effort: ThinkingEffort) => void;
  /** Current permission tier shown on the bottom bar and sent with chat turns. */
  currentPermissionMode?: PermissionMode;
  /** Set the permission tier. handleSlash routes bypass through the
   *  confirm picker instead of calling this directly. */
  setPermissionMode?: (mode: PermissionMode) => void;
  /** Toggle the terminal-bell-on-long-turn-complete flag. */
  toggleBell: () => boolean;
  /** Re-show the Welcome banner as a system note. */
  showWelcome: () => void;
  /** Print details for the current agent. */
  showAgentInfo: () => void;
  /** Export the current transcript to a markdown file. */
  exportTranscript: (filename?: string) => string;
  /** Override workspace seed writes for host adapters and deterministic
   *  failure tests. Normal TUI calls use node:fs writeFileSync. */
  writeWorkspaceSeed?: (path: string, content: string) => void;
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
  /** Submit a generated user turn (used by /review). */
  submitChat?: (text: string) => void;
}

const helpText = (): string => {
  const lines = ['Available commands:'];
  for (const c of allSlashCommands()) {
    const tag = c.source ? ` [${c.source}]` : '';
    lines.push(`  /${c.name.padEnd(14)} ${c.description}${tag}`);
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

/** Small shell-like tokenizer: quotes group arguments while Windows path
 * backslashes remain literal. It intentionally does not execute or expand. */
const tokenize = (s: string): string[] => {
  const tokens: string[] = [];
  let token = '';
  let quote = '';
  for (let i = 0; i < s.length; i++) {
    const char = s[i]!;
    if (quote) {
      if (char === quote) {
        quote = '';
      } else if (char === '\\' && s[i + 1] === quote) {
        token += quote;
        i++;
      } else {
        token += char;
      }
    } else if (char === '"' || char === "'") {
      quote = char;
    } else if (/\s/.test(char)) {
      if (token) {
        tokens.push(token);
        token = '';
      }
    } else {
      token += char;
    }
  }
  if (token) tokens.push(token);
  return tokens;
};

const requestError = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

type McpServer = {
  name: string;
  enabled: boolean;
  ready: boolean;
  error: string | null;
  tool_count: number;
  tools?: string[];
};

/** One row of the schema-driven settings list from /api/settings. */
type SettingRow = {
  key: string;
  value?: unknown;
  choices?: string[];
};

const mcpState = (server: McpServer): string => {
  if (!server.enabled || server.error === 'disabled') return 'disabled';
  if (server.ready) return 'ready';
  return server.error ? `error: ${server.error}` : 'starting';
};

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
  perm: 'permissions',
  permission: 'permissions',
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

const PERMISSION_MODES: PermissionMode[] = ['ask', 'acceptEdits', 'plan', 'auto', 'bypass'];

const normalizePermissionMode = (raw: string): PermissionMode | null => {
  const v = raw.toLowerCase();
  if (v === 'acceptedits' || v === 'accept-edits' || v === 'accept' || v === 'edits') return 'acceptEdits';
  if (v === 'ask' || v === 'plan' || v === 'auto' || v === 'bypass') return v;
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

    case 'keybindings':
      ctx.openPicker('shortcuts');
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

    case 'steer': {
      // Mid-run course-correction: inject a new instruction into the live run
      // for this session. The running loop picks it up at its next step.
      const conv = ctx.currentConversation;
      const message = args.join(' ').trim();
      if (!conv) { ctx.pushSystem('No active session to steer.'); return true; }
      if (!message) { ctx.pushSystem('Usage: /steer <new instruction>'); return true; }
      ctx.client.send({ action: 'steer', session_id: conv, message });
      return true;
    }

    case 'attended':
    case 'unattended': {
      // Toggle whether the agent may ask you questions on this session.
      const conv = ctx.currentConversation;
      if (!conv) { ctx.pushSystem('No active session.'); return true; }
      const on = cmd === 'attended'
        ? (args[0] !== 'off')   // /attended [on|off], default on
        : false;                // /unattended
      ctx.client.send({ action: 'set_attended', session_id: conv, attended: on });
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

    case 'jobs': {
      if (args[0]) {
        ctx.client.send({ action: 'get_job', job_id: args[0] });
      } else {
        const conv = ctx.currentConversation;
        if (!conv) { ctx.pushSystem('No active session.'); return true; }
        ctx.client.send({
          action: 'list_jobs',
          session_id: conv,
        });
        ctx.openPicker('jobs');
      }
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
        action: cmd === 'aliases' ? 'list_session_aliases' : 'list_sessions',
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
      ctx.client.send({ action: 'switch_model', model: args[0]!, session_id: ctx.currentConversation });
      return true;
    }

    case 'fetch-models':
    case 'fetch_models': {
      // /fetch-models <provider> — auto-discover models for a provider.
      // Hits the REST endpoint the settings UI uses; persists results
      // to custom_models in ~/.agentic/config.json. Next /model picker
      // will include the newly fetched models.
      if (args.length < 1) {
        ctx.pushSystem('Usage: /fetch-models <provider>  (e.g. anthropic, google, openai)');
        return true;
      }
      const provider = args[0]!;
      const base = backendBase();
      ctx.pushSystem(`Fetching models for ${provider}...`);
      void backendFetch(`${base}/api/providers/${encodeURIComponent(provider)}/fetch-models`, {
        method: 'POST',
      })
        .then((r) => r.json())
        .then((d: any) => {
          if (d?.error) {
            ctx.pushSystem(`Fetch failed: ${d.error}`);
          } else {
            ctx.pushSystem(
              `${provider}: fetched ${d.fetched} models, added ${d.added} new ` +
              `(total custom: ${d.total_custom})`,
            );
            ctx.client.send({ action: 'list_models' });
          }
        })
        .catch((e) => ctx.pushSystem(`Fetch error: ${e}`));
      return true;
    }

    case 'effort': {
      if (!ctx.setThinkingEffort) {
        ctx.pushSystem('/effort is not available in this screen.');
        return true;
      }
      if (args.length < 1) {
        ctx.openPicker('effort');
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

    case 'permissions': {
      if (!ctx.setPermissionMode) {
        ctx.pushSystem('/permissions is not available in this screen.');
        return true;
      }
      if (args.length < 1) {
        ctx.openPicker('permission');
        return true;
      }
      const mode = normalizePermissionMode(args[0]!);
      if (!mode) {
        ctx.pushSystem(`Unknown permission mode '${args[0]}'. Use one of: ${PERMISSION_MODES.join(', ')}`);
        return true;
      }
      if (mode === 'bypass') {
        // bypass never lands silently — route through the same confirm
        // the picker uses.
        ctx.openPicker('permission_bypass_confirm');
        return true;
      }
      ctx.setPermissionMode(mode);
      ctx.pushSystem(`Permission mode set to ${mode}.`);
      return true;
    }

    case 'resume': {
      // Refresh the conversation list before opening the picker — the
      // server's list_sessions action returns BOTH in-memory webui
      // sessions AND on-disk per-agent sessions (where channel-bound
      // chats live). Without this refresh the picker only shows the
      // history_list captured at connect time, which omits any
      // wechat / telegram sessions started by the channels worker.
      ctx.client.send({ action: 'list_sessions' });
      ctx.openPicker('resume');
      return true;
    }

    case 'branch': {
      const sid = ctx.currentConversation;
      if (!sid) {
        ctx.pushSystem('/branch needs an active conversation.');
        return true;
      }
      // /branch rename [name]  → empty name = AI auto-name
      // /branch delete         → delete the active branch's tail
      // /branch                → open picker (checkout)
      // The server falls back to the session's current head_id when
      // we don't pass head_msg_id, so these all operate on whatever
      // the session is currently checked out to.
      if (args[0] === 'rename') {
        const name = args.slice(1).join(' ').trim();
        if (!name) {
          ctx.client.send({ action: 'auto_name_branch', session_id: sid });
          ctx.pushSystem('Asking the model to name this branch…');
        } else {
          ctx.client.send({ action: 'rename_branch', session_id: sid, name });
          ctx.pushSystem(`Renamed current branch → ${name}`);
        }
        return true;
      }
      if (args[0] === 'delete') {
        ctx.client.send({ action: 'delete_branch', session_id: sid });
        ctx.pushSystem('Deleted current branch.');
        return true;
      }
      ctx.client.send({ action: 'list_branches', session_id: sid });
      ctx.openPicker('branch');
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
        ctx.client.send({ action: 'list_sessions' });
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
      // /channel rm <chan> <account_id>  → 删除一个 channel account
      //   (及其所有 binding). 之前要回 CLI 跑
      //   `openprogram channels accounts rm`, 现在 TUI 直接做.
      if (args[0] === 'rm' || args[0] === 'remove' || args[0] === 'delete') {
        if (args.length < 3) {
          ctx.pushSystem('Usage: /channel rm <channel> <account_id>');
          return true;
        }
        const [, channel, account_id] = args as [string, string, string];
        ctx.client.send({
          action: 'remove_channel_account',
          channel,
          account_id,
        } as never);
        ctx.pushSystem(`Removing ${channel}:${account_id}...`);
        return true;
      }
      // No-arg: multi-step picker → channel → account → bind action.
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

    case 'style': {
      // /style          → list styles, marking the active one
      // /style <name>   → switch
      // The style is a schema-declared setting, so both directions go
      // through /api/settings like every other config row.
      const url = `${backendBase()}/api/settings`;
      const readStyle = async (): Promise<{ value: string; choices: string[] }> => {
        const response = await backendFetch(url);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json() as { settings?: SettingRow[] };
        const row = (data.settings ?? []).find((s) => s.key === 'agent.output_style');
        if (!row) throw new Error('agent.output_style is not available on this server');
        return { value: String(row.value ?? 'default'), choices: row.choices ?? [] };
      };
      if (args.length < 1) {
        void readStyle()
          .then(({ value, choices }) => {
            const names = choices.length ? choices : [value];
            ctx.pushSystem(
              `Output style (how replies are written):\n${names
                .map((n) => `${n === value ? '●' : '○'} ${n}`)
                .join('\n')}\n\nSwitch with /style <name>.`,
            );
          })
          .catch((error) => ctx.pushSystem(`Style list failed: ${requestError(error)}`));
        return true;
      }
      const wanted = args[0]!;
      void readStyle()
        .then(async ({ choices }) => {
          if (choices.length && !choices.includes(wanted)) {
            ctx.pushSystem(
              `Unknown style '${wanted}'. Available: ${choices.join(', ')}`,
            );
            return;
          }
          const response = await backendFetch(url, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ key: 'agent.output_style', value: wanted }),
          });
          const result = await response.json() as { error?: string };
          if (!response.ok || result.error) {
            throw new Error(result.error ?? `HTTP ${response.status}`);
          }
          ctx.pushSystem(
            wanted === 'default'
              ? 'Output style set to default (no extra guidance).'
              : `Output style set to ${wanted}. Applies from the next turn.`,
          );
        })
        .catch((error) => ctx.pushSystem(`Style change failed: ${requestError(error)}`));
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
      // Token + cost stats live in the BottomBar; surface the latest here.
      ctx.pushSystem(
        'Current token usage is shown on the bottom bar. ↓ input, ↑ output.',
      );
      return true;
    }

    case 'web': {
      // Open the local web UI, authenticated. The browser gets the
      // fragment-bootstrap URL (token in the fragment, never sent to a
      // server); the transcript only ever shows the token-free URL.
      try {
        const urls = webUiUrls();
        if (urls) {
          openInBrowser(urls.bootstrap);
          ctx.pushSystem(`Web UI: ${urls.display}`);
        } else {
          ctx.pushSystem('Could not determine the web UI URL — no verified backend endpoint.');
        }
      } catch (e) {
        ctx.pushSystem(`/web failed: ${(e as Error).message}`);
      }
      return true;
    }

    case 'init': {
      try {
        const cwd = process.cwd();
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
        const created: string[] = [];
        const skipped: string[] = [];
        const writeSeed = ctx.writeWorkspaceSeed ?? writeFileSync;
        for (const [name, content] of seeds) {
          const path = join(cwd, name);
          if (existsSync(path)) {
            skipped.push(name);
          } else {
            writeSeed(path, content);
            created.push(name);
          }
        }
        ctx.pushSystem(
          `Initialized OpenProgram workspace at ${cwd}\n` +
          `Created: ${created.join(', ') || 'none'}\n` +
          `Skipped existing: ${skipped.join(', ') || 'none'}`,
        );
      } catch (e) {
        ctx.pushSystem(`/init failed: ${(e as Error).message}`);
      }
      return true;
    }

    case 'login': {
      const target = (args[0] ?? '').toLowerCase();
      // Claude subscription accounts — full in-TUI management (add via
      // browser login, activate / deactivate / rename / remove). No raw
      // commands: this opens the same panel the web Settings page has.
      if (!target || target === 'claude' || target === 'claude-code' || target === 'accounts') {
        ctx.openClaudeAccounts();
        return true;
      }
      // Chat channels have their own guided picker (account → QR / token).
      if (['wechat', 'telegram', 'discord', 'slack'].includes(target)) {
        ctx.openPicker('channel');
        return true;
      }
      // Any other target is a provider id — open the same in-TUI account panel
      // generically (list / add / activate / rename / remove). Add runs the
      // shared login flow (OAuth / device-code / import-from-CLI / API key)
      // right here, no punting to the web UI.
      ctx.openProviderAccounts(target);
      return true;
    }

    case 'logout': {
      // For claude-code, "log out" = deactivate / remove an account, which
      // the in-TUI panel does. Open it rather than printing a shell command.
      ctx.openClaudeAccounts();
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

    case 'config': {
      // Open the in-TUI settings editor. Ask the worker for the current
      // schema-resolved settings, then show the panel (pickerRouter renders
      // SettingsPanel from settingsRows; useWsEvents fills it on `settings`).
      ctx.client.send({ action: 'get_settings', session_id: ctx.currentConversation });
      ctx.openPicker('settings');
      return true;
    }

    case 'sandbox': {
      const conv = ctx.currentConversation;
      ctx.client.send({ action: 'sandbox', session_id: conv ?? '' });
      return true;
    }

    case 'rewind': {
      const conv = ctx.currentConversation;
      if (!conv) { ctx.pushSystem('No active session to rewind.'); return true; }
      ctx.client.send({ action: 'rewind', session_id: conv });
      return true;
    }

    case 'context': {
      const conv = ctx.currentConversation;
      ctx.client.send({ action: 'context', session_id: conv ?? '' });
      return true;
    }

    case 'review': {
      if (!ctx.submitChat) {
        ctx.pushSystem('Review is unavailable in this terminal session.');
        return true;
      }
      const target = args.join(' ').trim();
      ctx.submitChat(
        target
          ? `Review the current code changes against ${target}. Focus on correctness, regressions, security, and missing tests. Report findings first with file and line references; do not modify files.`
          : 'Review the current working-tree changes. Focus on correctness, regressions, security, and missing tests. Report findings first with file and line references; do not modify files.',
      );
      return true;
    }

    case 'memory': {
      const [verb = 'status', path, ...rest] = args;
      const base = `${backendBase()}/api/memory`;
      const report = (label: string, operation: Promise<Response>, render: (data: any) => string) => {
        void operation
          .then(async (response) => {
            const data = await response.json().catch(() => ({})) as Record<string, any>;
            if (!response.ok) throw new Error(data.error ?? data.detail ?? `HTTP ${response.status}`);
            ctx.pushSystem(render(data));
          })
          .catch((error) => ctx.pushSystem(`${label} failed: ${requestError(error)}`));
      };
      if (verb === 'status') {
        report('Memory status', backendFetch(`${base}/status`), (data) =>
          `Memory status\n${JSON.stringify(data, null, 2)}`);
        return true;
      }
      if (verb === 'list') {
        report('Memory list', backendFetch(`${base}/topics`), (data) => {
          const topics = Array.isArray(data) ? data : [];
          return topics.length
            ? `Memory topics:\n${topics.map((topic) => `• ${topic.path}${topic.title ? ` — ${topic.title}` : ''}`).join('\n')}`
            : 'Memory topics: none.';
        });
        return true;
      }
      if (verb === 'recall' || verb === 'search') {
        const query = [path, ...rest].filter(Boolean).join(' ');
        if (!query) {
          ctx.pushSystem('Usage: /memory recall <query>');
          return true;
        }
        report(
          'Memory recall',
          backendFetch(`${base}/refs?q=${encodeURIComponent(query)}&limit=8`),
          (data) => {
            const rows = Array.isArray(data) ? data : [];
            return rows.length
              ? rows.map((row) => `--- topics/${row.topic_path}#^${row.memory_id}\n${row.content}`).join('\n\n')
              : `No memories matched ${JSON.stringify(query)}.`;
          },
        );
        return true;
      }
      if (verb === 'show') {
        if (!path) {
          ctx.pushSystem('Usage: /memory show <topic-path|core>');
          return true;
        }
        const url = path === 'core'
          ? `${base}/core`
          : `${base}/topics/${encodeURIComponent(path)}`;
        report('Memory show', backendFetch(url), (data) =>
          `Memory ${path}\n\n${String(data.content ?? '')}`);
        return true;
      }
      if (verb === 'edit' || verb === 'set') {
        const content = rest.join(' ');
        if (!path || !content) {
          ctx.pushSystem('Usage: /memory edit <topic-path|core> <content> (quote content with spaces; use alt+enter for newlines)');
          return true;
        }
        const url = path === 'core'
          ? `${base}/core`
          : `${base}/topics/${encodeURIComponent(path)}`;
        report(
          'Memory edit',
          backendFetch(url, {
            method: 'PUT',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ content }),
          }),
          (data) => `Memory ${path} saved.${data.warning ? `\nWarning: ${data.warning}` : ''}`,
        );
        return true;
      }
      ctx.pushSystem('Usage: /memory [status|list|recall|show|edit]');
      return true;
    }

    case 'compact': {
      const session_id = ctx.currentConversation;
      if (!session_id) {
        ctx.pushSystem('No active session to compact.');
        return true;
      }
      ctx.client.send({ action: 'compact', session_id });
      return true;
    }

    case 'doctor': {
      void backendFetch(`${backendBase()}/api/doctor`)
        .then(async (response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const data = await response.json() as {
            results: Array<{ ok: boolean; label: string; detail: string }>;
            all_ok: boolean;
          };
          const results = data.results.map(
            (result) => `${result.ok ? '✓' : '✗'} ${result.label} - ${result.detail}`,
          );
          ctx.pushSystem(
            `Doctor report\n\n${results.join('\n')}\n\n${data.all_ok ? 'All checks passed.' : 'Some checks failed.'}`,
          );
        })
        .catch((error) => ctx.pushSystem(`Doctor check failed: ${requestError(error)}`));
      return true;
    }

    case 'mcp': {
      const [verb = 'list', name] = args;
      const base = `${backendBase()}/api/mcp/servers`;
      if (verb === 'list') {
        void backendFetch(base)
          .then(async (response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json() as { servers?: McpServer[] };
            const servers = data.servers ?? [];
            ctx.pushSystem(servers.length
              ? `MCP servers:\n${servers.map((server) =>
                `${mcpState(server) === 'ready' ? '✓' : '✗'} ${server.name} — ${mcpState(server)} (${server.tool_count} tools)`,
              ).join('\n')}`
              : 'MCP servers: none configured.');
          })
          .catch((error) => ctx.pushSystem(`MCP list failed: ${requestError(error)}`));
        return true;
      }
      if (verb === 'show') {
        if (!name) {
          ctx.pushSystem('Usage: /mcp show <name>');
          return true;
        }
        void backendFetch(`${base}/${encodeURIComponent(name)}`)
          .then(async (response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const server = await response.json() as McpServer;
            ctx.pushSystem(
              `MCP server: ${server.name}\nstate: ${mcpState(server)}\ntools: ${(server.tools ?? []).join(', ') || 'none'}`,
            );
          })
          .catch((error) => ctx.pushSystem(`MCP show failed: ${requestError(error)}`));
        return true;
      }
      if (verb === 'restart' || verb === 'enable' || verb === 'disable') {
        if (!name) {
          ctx.pushSystem(`Usage: /mcp ${verb} <name>`);
          return true;
        }
        const result = verb === 'restart' ? 'restarted' : `${verb}d`;
        void backendFetch(`${base}/${encodeURIComponent(name)}/${verb}`, { method: 'POST' })
          .then((response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            ctx.pushSystem(`MCP server ${name} ${result}.`);
          })
          .catch((error) => ctx.pushSystem(`MCP ${verb} failed for ${name}: ${requestError(error)}`));
        return true;
      }
      if (verb === 'add') {
        if (!name) {
          ctx.pushSystem('Usage: /mcp add <name> <command> [args…] [--env KEY=VALUE] [--timeout N] [--disabled]');
          return true;
        }
        const command: string[] = [];
        const env: Record<string, string> = {};
        let timeout = 30;
        let enabled = true;
        for (let i = 2; i < args.length; i++) {
          const value = args[i]!;
          if (value === '--disabled') {
            enabled = false;
          } else if (value === '--timeout' && args[i + 1]) {
            timeout = Number(args[++i]);
          } else if (value === '--env' && args[i + 1]) {
            const item = args[++i]!;
            const split = item.indexOf('=');
            if (split <= 0) {
              ctx.pushSystem(`Invalid --env value: ${item}. Expected KEY=VALUE.`);
              return true;
            }
            env[item.slice(0, split)] = item.slice(split + 1);
          } else {
            command.push(value);
          }
        }
        if (!command.length || !Number.isFinite(timeout) || timeout <= 0) {
          ctx.pushSystem('MCP add requires a command and a positive --timeout.');
          return true;
        }
        void backendFetch(base, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ name, type: 'local', command, env, enabled, timeout_seconds: timeout }),
        })
          .then(async (response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            ctx.pushSystem(`MCP server ${name} added.`);
          })
          .catch((error) => ctx.pushSystem(`MCP add failed for ${name}: ${requestError(error)}`));
        return true;
      }
      if (verb === 'edit') {
        const [field, ...values] = args.slice(2);
        if (!name || !field || !values.length) {
          ctx.pushSystem('Usage: /mcp edit <name> <command|timeout|enabled|url|env> <value…>');
          return true;
        }
        let body: Record<string, unknown> | null = null;
        if (field === 'command') body = { type: 'local', command: values };
        if (field === 'timeout') body = { timeout_seconds: Number(values[0]) };
        if (field === 'enabled' && /^(true|false)$/i.test(values[0]!)) {
          body = { enabled: values[0]!.toLowerCase() === 'true' };
        }
        if (field === 'url') body = { type: 'remote', url: values.join(' ') };
        if (field === 'env') {
          const split = values[0]!.indexOf('=');
          if (split > 0) body = { env: { [values[0]!.slice(0, split)]: values[0]!.slice(split + 1) } };
        }
        if (!body || (field === 'timeout' && (!Number.isFinite(body.timeout_seconds) || Number(body.timeout_seconds) <= 0))) {
          ctx.pushSystem(`Invalid MCP edit field/value: ${field} ${values.join(' ')}`);
          return true;
        }
        void backendFetch(`${base}/${encodeURIComponent(name)}`, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify(body),
        })
          .then((response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            ctx.pushSystem(`MCP server ${name} updated.`);
          })
          .catch((error) => ctx.pushSystem(`MCP edit failed for ${name}: ${requestError(error)}`));
        return true;
      }
      if (verb === 'remove' || verb === 'rm' || verb === 'delete') {
        if (!name) {
          ctx.pushSystem(`Usage: /mcp ${verb} <name>`);
          return true;
        }
        void backendFetch(`${base}/${encodeURIComponent(name)}`, { method: 'DELETE' })
          .then((response) => {
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            ctx.pushSystem(`MCP server ${name} removed.`);
          })
          .catch((error) => ctx.pushSystem(`MCP remove failed for ${name}: ${requestError(error)}`));
        return true;
      }
      ctx.pushSystem('Usage: /mcp [list|show|add|edit|remove|restart|enable|disable] [name]');
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
