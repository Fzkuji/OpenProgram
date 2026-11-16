/**
 * REST client for a provider's credential list, shared by the TUI accounts
 * picker. Hits the endpoints the web Settings → LLM Providers → Credentials
 * section uses (/api/providers/{id}/credentials/*), so the two surfaces
 * behave the same: an ordered list of keys and signed-in accounts, used top
 * to bottom (or evenly, or least used first), each of which can be checked,
 * renamed, set aside, moved or removed. A key is never read back; a row
 * carries only its last four characters.
 *
 * Adding goes through the shared login endpoints (/login/*), whose api_key
 * method asks for the key.
 */
import { backendBase, backendFetch } from './backend.js';

export interface LoginMethod {
  id: string;
  label: string;
}

export type CredentialStatus = 'ready' | 'cooling' | 'quota' | 'invalid_key' | 'needs_login';

/** One row of GET /api/providers/{id}/credentials. */
export interface CredentialRow {
  id: string;
  label: string;
  display_label: string;
  kind: string;
  source: string;
  identity: { email?: string; plan?: string };
  key_tail: string;
  env_var: string | null;
  read_only: boolean;
  enabled: boolean;
  status: CredentialStatus;
  cooldown_until: number | null;
}

/** Shape of GET /api/providers/{id}/credentials. */
export interface CredentialList {
  provider: string;
  strategy: string;
  strategies: string[];
  /** The credential a new conversation would use now. */
  active: string | null;
  auth: { methods: LoginMethod[]; env_vars: string[]; check: string | null };
  credentials: CredentialRow[];
}

export const EMPTY_CREDENTIALS: CredentialList = {
  provider: '', strategy: 'in_order', strategies: [], active: null,
  auth: { methods: [], env_vars: [], check: null }, credentials: [],
};

/** A key (pasted or from the environment) rather than a signed-in account. */
export const isKey = (row: Pick<CredentialRow, 'kind'>): boolean =>
  row.kind === 'api_key' || row.kind === 'credential_process';

/** The ways to add a credential: the provider's sign-in methods, plus
 *  pasting a key when the provider takes keys. */
export function addMethods(list: CredentialList): LoginMethod[] {
  const takesKeys = list.auth.env_vars.length > 0 || !!list.auth.check;
  const methods = list.auth.methods.filter((m) => m.id !== 'api_key');
  return takesKeys || !methods.length ? [...methods, { id: 'api_key', label: 'Paste an API key' }] : methods;
}

/** The word after a row: "ready", "back 14:32", "out of quota", "invalid
 *  key", "sign in again" or "off". */
export function statusWord(row: CredentialRow, now = Date.now() / 1000): string {
  if (!row.enabled) return 'off';
  switch (row.status) {
    case 'cooling': {
      if (!row.cooldown_until || row.cooldown_until <= now) return 'rate limited';
      const d = new Date(row.cooldown_until * 1000);
      return `back ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    case 'quota': return 'out of quota';
    case 'invalid_key': return 'invalid key';
    case 'needs_login': return 'sign in again';
    default: return 'ready';
  }
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

async function send(path: string, method: string, body?: unknown): Promise<any> {
  const r = await backendFetch(`${backendBase()}${path}`, {
    method,
    headers: JSON_HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return r.json();
}

const postTo = (path: string, body: unknown) => send(path, 'POST', body ?? {});

async function getJson(path: string): Promise<any> {
  const r = await backendFetch(`${backendBase()}${path}`);
  return r.json();
}

export interface CheckResult {
  credential?: CredentialRow;
  check?: { result?: string; message?: string };
  error?: string;
}

export interface CredentialsClient {
  providerId: string;
  fetch(): Promise<CredentialList>;
  check(id: string): Promise<CheckResult>;
  rename(id: string, label: string): Promise<{ error?: string }>;
  setEnabled(id: string, enabled: boolean): Promise<{ error?: string }>;
  remove(id: string): Promise<{ error?: string; env_var?: string }>;
  reorder(order: string[]): Promise<{ error?: string }>;
  setStrategy(strategy: string): Promise<{ error?: string }>;
}

/** Build a client bound to one provider id. */
export function makeCredentialsClient(providerId: string): CredentialsClient {
  const base = `/api/providers/${encodeURIComponent(providerId)}/credentials`;
  const one = (id: string) => `${base}/${encodeURIComponent(id)}`;
  return {
    providerId,
    fetch: () => getJson(base) as Promise<CredentialList>,
    check: (id) => send(`${one(id)}/check`, 'POST'),
    rename: (id, label) => send(one(id), 'PATCH', { label }),
    setEnabled: (id, enabled) => send(one(id), 'PATCH', { enabled }),
    remove: (id) => send(one(id), 'DELETE'),
    reorder: (order) => postTo(`${base}/reorder`, { order }),
    setStrategy: (strategy) => postTo(`${base}/strategy`, { strategy }),
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
