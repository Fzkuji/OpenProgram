/**
 * REST client for per-provider account management, shared by the TUI accounts
 * picker. Hits the exact endpoints the web Settings UI uses
 * (/api/providers/{id}/accounts/*), so the two surfaces stay behaviourally
 * identical and we don't duplicate the logic.
 *
 * One client per provider via makeAccountsClient(providerId): claude-code is
 * just providerId='claude-code' (its routes are Meridian-backed); every other
 * provider is served generically from the AuthStore. The backend tells us how
 * "add account" works via `add_mode`:
 *   - "code_paste"  claude-code's interactive OAuth (startAdd → submitCode)
 *   - "login"       the shared /login/* flow (startLogin → pollLogin → submitLogin)
 */
import { backendBase } from './backend.js';

export interface LoginMethod {
  id: string;
  label: string;
}

export interface AccountInfo {
  name: string;
  email?: string;
  kind?: string;
  status?: string;
  count?: number;
}

/** Shape of GET /api/providers/{id}/accounts. */
export interface AccountsState {
  installed: boolean;
  ready: boolean;
  active: string | null;
  accounts: AccountInfo[];
  add_mode?: 'code_paste' | 'login' | 'api_key';
  login_methods?: LoginMethod[];
  rotation?: boolean;
  strategy?: string;
}

/** Result of starting a code-paste add (the OAuth login URL + a session). */
export interface AddStarted {
  session?: string;
  url?: string;
  name?: string;
  error?: string;
}

/** One /login/poll response (the shared login flow used by login-mode add). */
export interface LoginPoll {
  events?: Array<{
    type: string;
    url?: string;
    message?: string;
    user_code?: string;
    verification_uri?: string;
  }>;
  cursor?: number;
  waiting?: boolean;
  prompt?: { message: string; secret?: boolean };
  done?: boolean;
  ok?: boolean;
  error?: string;
  name?: string;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function postTo(path: string, body: unknown): Promise<any> {
  const r = await fetch(`${backendBase()}${path}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body ?? {}),
  });
  return r.json();
}

async function getJson(path: string): Promise<any> {
  const r = await fetch(`${backendBase()}${path}`);
  return r.json();
}

export interface AccountsClient {
  providerId: string;
  fetchAccounts(): Promise<AccountsState>;
  startAdd(name: string): Promise<AddStarted>;
  submitCode(session: string, code: string): Promise<{ ok?: boolean; error?: string; name?: string }>;
  useAccount(name: string): Promise<{ ok?: boolean; error?: string; active?: string }>;
  removeAccount(name: string): Promise<{ ok?: boolean; error?: string; removed?: boolean }>;
  renameAccount(oldName: string, newName: string): Promise<{ ok?: boolean; error?: string; name?: string }>;
  validateAccount(name: string): Promise<{ ok?: boolean; status?: string; detail?: string; error?: string }>;
  revealKey(name: string): Promise<{ ok?: boolean; value?: string; error?: string }>;
  setRotation(enabled: boolean, strategy?: string): Promise<{ ok?: boolean; enabled?: boolean; strategy?: string }>;
}

/** Build a client bound to one provider id. */
export function makeAccountsClient(providerId: string): AccountsClient {
  const base = `/api/providers/${encodeURIComponent(providerId)}/accounts`;
  return {
    providerId,
    fetchAccounts: () => getJson(base) as Promise<AccountsState>,
    startAdd: (name) => postTo(`${base}/add`, { name }),
    submitCode: (session, code) => postTo(`${base}/add/code`, { session, code }),
    useAccount: (name) => postTo(`${base}/use`, { name }),
    removeAccount: (name) => postTo(`${base}/remove`, { name }),
    renameAccount: (oldName, newName) => postTo(`${base}/rename`, { old: oldName, new: newName }),
    validateAccount: (name) => postTo(`${base}/${encodeURIComponent(name)}/validate`, {}),
    revealKey: (name) => getJson(`${base}/${encodeURIComponent(name)}/reveal`),
    setRotation: (enabled, strategy) => postTo(`${base}/rotation`, { enabled, strategy }),
  };
}

// ---- login-mode add: the shared /login/* flow ---------------------------
// Used when a provider reports add_mode="login" (OAuth / device-code /
// import-from-CLI). `profile` is the new account name the credential lands in.

export async function startLogin(
  providerId: string,
  method: string,
  profile: string,
): Promise<{ session?: string; method?: string; error?: string }> {
  return postTo(`/api/providers/${encodeURIComponent(providerId)}/login/start`, { method, profile });
}

export async function pollLogin(
  providerId: string,
  session: string,
  cursor: number,
): Promise<LoginPoll> {
  return getJson(
    `/api/providers/${encodeURIComponent(providerId)}/login/poll?session=${encodeURIComponent(session)}&cursor=${cursor}`,
  );
}

export async function submitLogin(
  providerId: string,
  session: string,
  value: string,
): Promise<{ ok?: boolean }> {
  return postTo(`/api/providers/${encodeURIComponent(providerId)}/login/submit`, { session, value });
}

export async function cancelLogin(providerId: string, session: string): Promise<{ ok?: boolean }> {
  return postTo(`/api/providers/${encodeURIComponent(providerId)}/login/cancel`, { session });
}
