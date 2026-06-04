/**
 * REST client for the claude-code provider's Claude-account management,
 * shared by the TUI accounts picker. These hit the exact endpoints the
 * web Settings UI uses (webui/routes/providers.py), so the two surfaces
 * stay behaviourally identical and we don't duplicate the logic.
 *
 * The underlying local proxy is an internal detail — nothing here names
 * it; callers only ever see "Claude account".
 */
import { backendBase } from './backend.js';

export interface ClaudeAccount {
  name: string;
  email?: string;
}

/** Shape of GET /api/providers/claude-code/accounts. */
export interface ClaudeAccountsState {
  installed: boolean;
  ready: boolean;
  active: string | null;
  accounts: ClaudeAccount[];
}

/** Result of starting an add (the OAuth login URL + a session token). */
export interface AddStarted {
  session?: string;
  url?: string;
  name?: string;
  error?: string;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function post(path: string, body: unknown): Promise<any> {
  const r = await fetch(`${backendBase()}${path}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body ?? {}),
  });
  return r.json();
}

export async function fetchAccounts(): Promise<ClaudeAccountsState> {
  const r = await fetch(`${backendBase()}/api/providers/claude-code/accounts`);
  return (await r.json()) as ClaudeAccountsState;
}

/** Begin a browser login. Auto-installs/starts the backend on first use. */
export async function startAdd(name: string): Promise<AddStarted> {
  return post('/api/providers/claude-code/accounts/add', { name });
}

/** Finish the login by submitting the code the user pasted. */
export async function submitCode(
  session: string,
  code: string,
): Promise<{ ok?: boolean; error?: string; name?: string }> {
  return post('/api/providers/claude-code/accounts/add/code', { session, code });
}

/** Activate one account — or pass "" to deactivate (run on none). */
export async function useAccount(name: string): Promise<{ ok?: boolean; error?: string }> {
  return post('/api/providers/claude-code/accounts/use', { name });
}

export async function removeAccount(name: string): Promise<{ ok?: boolean; error?: string }> {
  return post('/api/providers/claude-code/accounts/remove', { name });
}

export async function renameAccount(
  oldName: string,
  newName: string,
): Promise<{ ok?: boolean; error?: string }> {
  return post('/api/providers/claude-code/accounts/rename', { old: oldName, new: newName });
}
