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
import { backendBase, backendFetch } from './backend.js';

export interface LoginMethod {
  id: string;
  label: string;
}

export interface AccountInfo {
  id?: string;
  label?: string;
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
  label?: string;
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function postTo(path: string, body: unknown): Promise<any> {
  const r = await backendFetch(`${backendBase()}${path}`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body ?? {}),
  });
  return r.json();
}

async function getJson(path: string): Promise<any> {
  const r = await backendFetch(`${backendBase()}${path}`);
  return r.json();
}

export interface AccountsClient {
  providerId: string;
  fetchAccounts(): Promise<AccountsState>;
  startAdd(name: string): Promise<AddStarted>;
  submitCode(session: string, code: string): Promise<{ ok?: boolean; error?: string; name?: string }>;
  useAccount(id: string): Promise<{ ok?: boolean; error?: string; active?: string }>;
  removeAccount(id: string): Promise<{ ok?: boolean; error?: string; removed?: boolean }>;
  renameAccount(id: string, label: string): Promise<{ ok?: boolean; error?: string; id?: string; label?: string; name?: string }>;
  validateAccount(id: string): Promise<{ ok?: boolean; status?: string; detail?: string; error?: string }>;
  revealKey(id: string): Promise<{ ok?: boolean; value?: string; error?: string }>;
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
    useAccount: (id) => postTo(`${base}/use`, { id }),
    removeAccount: (id) => postTo(`${base}/remove`, { id }),
    renameAccount: (id, label) => postTo(`${base}/rename`, { id, name: label }),
    validateAccount: (id) => postTo(`${base}/${encodeURIComponent(id)}/validate?ping=true`, {}),
    revealKey: (id) => getJson(`${base}/${encodeURIComponent(id)}/reveal`),
    setRotation: (enabled, strategy) => postTo(`${base}/rotation`, { enabled, strategy }),
  };
}

// ---- login-mode add: the shared /login/* flow ---------------------------
// Used when a provider reports add_mode="login" (OAuth / device-code /
// import-from-CLI). `label` is optional display text; the worker allocates the
// stable account id independently.

export async function startLogin(
  providerId: string,
  method: string,
  label: string,
): Promise<{ session?: string; method?: string; error?: string }> {
  return postTo(`/api/providers/${encodeURIComponent(providerId)}/login/start`, { method, label });
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
