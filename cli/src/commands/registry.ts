export interface SlashCommand {
  name: string;
  description: string;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  { name: 'help', description: 'Show available commands' },
  { name: 'agents', description: 'List or switch agents' },
  { name: 'agent', description: 'Switch to a different agent' },
  { name: 'model', description: 'Change the model' },
  { name: 'session', description: 'Show current session info' },
  { name: 'sessions', description: 'List sessions' },
  { name: 'new', description: 'Start a new session' },
  { name: 'resume', description: 'Resume a previous session' },
  { name: 'search', description: 'Search across past sessions' },
  { name: 'clear', description: 'Clear the screen' },
  { name: 'compact', description: 'Compact the conversation' },
  { name: 'config', description: 'Open configuration' },
  { name: 'login', description: 'Sign in to a provider or channel' },
  { name: 'logout', description: 'Sign out of the current account' },
  { name: 'memory', description: 'View or edit memory' },
  { name: 'mcp', description: 'Manage MCP servers' },
  { name: 'cost', description: 'Show token + cost usage' },
  { name: 'tools', description: 'Toggle tools availability for next turn' },
  { name: 'export', description: 'Export the current transcript to a file' },
  { name: 'doctor', description: 'Run health diagnostics' },
  { name: 'review', description: 'Review the diff' },
  { name: 'init', description: 'Initialize an OpenProgram workspace' },
  { name: 'attach', description: 'Attach a channel peer to this session' },
  { name: 'detach', description: 'Detach a channel peer' },
  { name: 'connections', description: 'List channel bindings' },
  { name: 'copy', description: 'Copy the last assistant reply' },
  { name: 'bell', description: 'Toggle terminal-bell on long turns' },
  { name: 'welcome', description: 'Re-show the welcome banner' },
  { name: 'quit', description: 'Exit OpenProgram' },
];
