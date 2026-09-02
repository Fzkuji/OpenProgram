import { backendBase, backendFetch } from '../utils/backend.js';

export interface SlashCommand {
  name: string;
  description: string;
  /** Registry layer for backend-sourced commands ("skill" | "user" | ...);
   *  unset for TUI-local commands. */
  source?: string;
}

/** TUI-local actions — each has a handler in handler.ts / REPL.tsx.
 *  Everything else (skill / user / project / plugin / mcp commands)
 *  comes from the worker's unified registry via loadBackendCommands. */
export const SLASH_COMMANDS: SlashCommand[] = [
  { name: 'help', description: 'Show available commands' },
  { name: 'agents', description: 'List or switch agents' },
  { name: 'agent', description: 'Switch to a different agent' },
  { name: 'model', description: 'Change the model' },
  { name: 'fetch-models', description: 'Auto-discover models from a provider (e.g. /fetch-models anthropic)' },
  { name: 'effort', description: 'Set thinking effort (off/minimal/low/medium/high/xhigh)' },
  { name: 'permissions', description: 'Set permission mode (ask/acceptEdits/plan/auto/bypass); shift+tab cycles' },
  { name: 'session', description: 'Show current session info' },
  { name: 'sessions', description: 'List sessions' },
  { name: 'jobs', description: 'Show canonical resource state for background jobs' },
  { name: 'steer', description: 'Apply an instruction to the current execution at its next safe point' },
  { name: 'fork', description: 'Create an execution branch from a checkpoint and revision manifest' },
  { name: 'retry', description: 'Create a same-revision execution retry from a checkpoint' },
  { name: 'new', description: 'Start a new session' },
  { name: 'resume', description: 'Resume a previous session' },
  { name: 'search', description: 'Search across past sessions' },
  { name: 'clear', description: 'Clear the screen' },
  { name: 'compact', description: 'Compact the conversation' },
  { name: 'context', description: 'Show context token distribution' },
  { name: 'sandbox', description: 'Toggle system sandbox (restrict bash to cwd writes only)' },
  { name: 'rewind', description: 'Rewind to a previous point (restore code and/or conversation)' },
  { name: 'config', description: 'Open configuration' },
  { name: 'login', description: 'Manage Claude accounts (add / switch / rename); /login <channel> for chat channels' },
  { name: 'logout', description: 'Open the Claude account panel to deactivate or remove an account' },
  { name: 'memory', description: 'View or edit memory' },
  { name: 'mcp', description: 'Manage MCP servers' },
  { name: 'cost', description: 'Show token + cost usage' },
  { name: 'tools', description: 'Toggle tools availability for next turn' },
  { name: 'export', description: 'Export the current transcript to a file' },
  { name: 'doctor', description: 'Run health diagnostics' },
  { name: 'review', description: 'Review the diff' },
  { name: 'diff', description: 'Show git working-tree diff' },
  { name: 'init', description: 'Initialize an OpenProgram workspace' },
  { name: 'channel', description: 'Connect this conversation to a chat channel (wechat/telegram/...)' },
  { name: 'browser', description: 'Drive Chrome — /browser <url> or /browser <verb>' },
  { name: 'attach', description: 'Attach a channel peer to this session' },
  { name: 'detach', description: 'Detach a channel peer' },
  { name: 'connections', description: 'List channel bindings' },
  { name: 'copy', description: 'Copy the last assistant reply' },
  { name: 'bell', description: 'Toggle terminal-bell on long turns' },
  { name: 'theme', description: 'Switch the color theme (dark / light / dim)' },
  { name: 'style', description: 'Show or set the output style (how replies are written)' },
  { name: 'welcome', description: 'Re-show the welcome banner' },
  { name: 'quit', description: 'Exit OpenProgram' },
];

let backendCommands: SlashCommand[] = [];

/** Fetch the unified command registry (skill / user / project / plugin /
 *  mcp layers) from the worker's /api/commands and cache it for
 *  completion + help. TUI-local names win on collision. Safe to call
 *  again to refresh; a fetch failure keeps the previous snapshot. */
export async function loadBackendCommands(): Promise<void> {
  try {
    const r = await backendFetch(`${backendBase()}/api/commands`);
    if (!r.ok) return;
    const d = (await r.json()) as { commands?: Array<Record<string, unknown>> };
    const locals = new Set(SLASH_COMMANDS.map((c) => c.name));
    backendCommands = (d.commands ?? [])
      .filter((c) => c
        && typeof c.name === 'string'
        && c.user_invocable !== false
        && !c.hidden
        && !locals.has(c.name as string))
      .map((c) => ({
        name: c.name as string,
        description: (c.description as string) || `${(c.source_label as string) || (c.source as string)} command`,
        source: c.source as string,
      }));
  } catch {
    /* worker unreachable — local commands still work */
  }
}

/** TUI-local commands + backend registry commands, for completion,
 *  the ctrl+K palette, and /help. */
export const allSlashCommands = (): SlashCommand[] =>
  [...SLASH_COMMANDS, ...backendCommands];

export const findBackendCommand = (name: string): SlashCommand | undefined =>
  backendCommands.find((c) => c.name === name);

export interface InvokeResult {
  ok: boolean;
  kind: string;
  rendered: string;
  error?: string;
}

/** Resolve + render a registry command via POST /api/commands/invoke.
 *  Returns null when the worker is unreachable. */
export async function invokeBackendCommand(
  text: string,
  sessionId?: string,
): Promise<InvokeResult | null> {
  try {
    const r = await backendFetch(`${backendBase()}/api/commands/invoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, session_id: sessionId ?? '' }),
    });
    if (!r.ok) return null;
    return (await r.json()) as InvokeResult;
  } catch {
    return null;
  }
}
