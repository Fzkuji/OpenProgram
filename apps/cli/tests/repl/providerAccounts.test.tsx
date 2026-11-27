import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { buildProviderAccountsPicker } from '../../src/screens/repl/pickers/providerAccounts.js';
import { ProviderLoginFlow } from '../../src/screens/repl/pickers/providerLoginFlow.js';
import type { PickerCtx } from '../../src/screens/repl/pickerRouter.js';
import type { AddStarted, CredentialList, CredentialRow } from '../../src/utils/providerAccounts.js';

vi.mock('../../src/utils/backend.js', async () => {
  const actual = await vi.importActual<typeof import('../../src/utils/backend.js')>('../../src/utils/backend.js');
  return { ...actual, openInBrowser: vi.fn() };
});

// We assert on the returned React element's PROPS rather than rendering it.
// The picker builder returns a <Picker> or <LineInput> with fully-formed
// items / handlers, so prop inspection exercises all the panel's logic
// (which rows, what each selection does) without needing Ink to render.

const flush = () => new Promise((r) => setTimeout(r, 10));

interface Item { label: string; value: string; description?: string }
const items = (el: any): Item[] => el.props.items as Item[];
const labels = (el: any): string[] => items(el).map((i) => i.label);

const row = (id: string, fields: Partial<CredentialRow> = {}): CredentialRow => ({
  id, label: '', display_label: id, kind: 'api_key', source: 'web', identity: {}, key_tail: id.slice(0, 4),
  env_var: null, read_only: false, enabled: true, status: 'ready', cooldown_until: null, ...fields,
});

const KEYS: CredentialList = {
  provider: 'openai', strategy: 'in_order', strategies: ['in_order', 'evenly', 'least_used'], active: 'a1b2',
  auth: { methods: [], env_vars: ['OPENAI_API_KEY'], check: 'models' },
  credentials: [
    row('a1b2', { display_label: 'Personal' }),
    row('work', { display_label: 'Work', status: 'invalid_key' }),
    row('env:OPENAI_API_KEY', { display_label: 'OPENAI_API_KEY', env_var: 'OPENAI_API_KEY', source: 'env', read_only: true, key_tail: 'zz99' }),
  ],
};

function makeCtx(over: Partial<PickerCtx> = {}): PickerCtx {
  const base = {
    pushSystem: vi.fn(),
    accountsProviderId: 'openai',
    accountsState: KEYS,
    accountSelected: null as string | null,
    accountPendingAdd: null as AddStarted | null,
    accountLogin: null as { name: string; method: string } | null,
    setAccountsProviderId: vi.fn(),
    setAccountsState: vi.fn(),
    setAccountSelected: vi.fn(),
    setAccountPendingAdd: vi.fn(),
    setAccountLogin: vi.fn(),
    setPickerKind: vi.fn(),
  };
  return { ...base, ...over } as unknown as PickerCtx;
}

// A sign-in provider: its accounts come from the shared /login/* flow.
function loginCtx(over: Partial<PickerCtx> = {}): PickerCtx {
  return makeCtx({
    accountsProviderId: 'qwen',
    accountsState: {
      ...KEYS, provider: 'qwen', active: null,
      auth: { methods: [{ id: 'import_from_cli', label: 'Import existing login' }], env_vars: [], check: null },
      credentials: [],
    },
    ...over,
  });
}

describe('Provider credentials panel — list', () => {
  it('lists each credential with its tail and state, the one in use marked', () => {
    const el = buildProviderAccountsPicker(makeCtx(), 'acct_list')!;
    expect(el.props.title).toBe('openai credentials — used in order');
    const [personal, work, env] = items(el);
    expect(personal.label).toBe('→ Personal   ····a1b2');
    expect(personal.description).toBe('in use · ready');
    expect(work.description).toBe('invalid key');
    expect(env.label).toContain('$OPENAI_API_KEY');
    expect(env.label).not.toContain('zz99');
    expect(labels(el)).toContain('⟳ Order: in order');
    expect(labels(el)).toContain('＋ Add key');
  });

  it('selecting a credential opens its menu by id', () => {
    const ctx = makeCtx();
    buildProviderAccountsPicker(ctx, 'acct_list')!.props.onSelect({ value: 'acct:work' });
    expect(ctx.setAccountSelected).toHaveBeenCalledWith('work');
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_action');
  });

  it('an empty sign-in provider says none yet and offers its sign-in', () => {
    const el = buildProviderAccountsPicker(loginCtx(), 'acct_list')!;
    expect(el.props.title).toContain('none yet');
    expect(labels(el).some((l) => l.includes('Order'))).toBe(false);
    expect(items(el).at(-1)?.description).toBe('Import existing login');
  });
});

describe('Provider credentials panel — row menu', () => {
  it('offers check, rename, set aside and the moves that fit the row; no key is ever shown', () => {
    const first = labels(buildProviderAccountsPicker(makeCtx({ accountSelected: 'a1b2' }), 'acct_action')!);
    expect(first).toEqual(['Check now', 'Rename', "Don't use for now", 'Move down', 'Remove', '← Back']);
    const middle = labels(buildProviderAccountsPicker(makeCtx({ accountSelected: 'work' }), 'acct_action')!);
    expect(middle).toContain('Move up');
    expect(middle).toContain('Move down');
    expect(first.some((l) => /reveal/i.test(l))).toBe(false);
  });

  it('an environment key can be checked, set aside and moved, not renamed or removed', () => {
    const el = buildProviderAccountsPicker(makeCtx({ accountSelected: 'env:OPENAI_API_KEY' }), 'acct_action')!;
    expect(labels(el)).toEqual(['Check now', "Don't use for now", 'Move up', '← Back']);
  });

  it('Rename routes to the rename step seeded with the name', () => {
    const ctx = makeCtx({ accountSelected: 'a1b2' });
    buildProviderAccountsPicker(ctx, 'acct_action')!.props.onSelect({ value: 'rename' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_rename');
    const step = buildProviderAccountsPicker(ctx, 'acct_rename')!;
    expect(step.props.initial).toBe('Personal');
  });
});

describe('Provider credentials panel — add', () => {
  it('a key provider adds through the api_key method of the shared login flow', () => {
    const ctx = makeCtx();
    buildProviderAccountsPicker(ctx, 'acct_list')!.props.onSelect({ value: '__add__' });
    expect(ctx.setAccountLogin).toHaveBeenCalledWith({ name: '', method: 'api_key' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_login_name');
  });

  it('a single sign-in way skips the method picker', () => {
    const ctx = loginCtx();
    buildProviderAccountsPicker(ctx, 'acct_login_name')!.props.onSubmit('work');
    expect(ctx.setAccountLogin).toHaveBeenCalledWith({ name: 'work', method: 'import_from_cli' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_login');
  });

  it('the login step forwards the provider, method and name to ProviderLoginFlow', () => {
    const el = buildProviderAccountsPicker(makeCtx({ accountLogin: { name: 'Work', method: 'api_key' } }), 'acct_login')!;
    expect(el.type).toBe(ProviderLoginFlow);
    expect(el.props.providerId).toBe('openai');
    expect(el.props.method).toBe('api_key');
    expect(el.props.accountLabel).toBe('Work');
  });
});

describe('Provider credentials panel — REST wiring', () => {
  let calls: Array<{ url: string; method?: string; body: any }>;
  beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', (url: string, opts?: any) => {
      calls.push({ url: String(url), method: opts?.method ?? 'GET', body: opts?.body ? JSON.parse(opts.body) : null });
      const body = String(url).endsWith('/credentials') && !opts?.method ? KEYS : { ok: true };
      return Promise.resolve({ json: () => Promise.resolve(body) } as Response);
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  const sent = (method: string, suffix: string) =>
    calls.find((c) => c.method === method && c.url.endsWith(suffix));

  it('Move down posts the new order', async () => {
    buildProviderAccountsPicker(makeCtx({ accountSelected: 'a1b2' }), 'acct_action')!.props.onSelect({ value: 'down' });
    await flush();
    expect(sent('POST', '/openai/credentials/reorder')?.body).toEqual({ order: ['work', 'a1b2', 'env:OPENAI_API_KEY'] });
  });

  it("Don't use for now patches enabled off; Remove deletes by encoded id", async () => {
    buildProviderAccountsPicker(makeCtx({ accountSelected: 'a1b2' }), 'acct_action')!.props.onSelect({ value: 'disable' });
    buildProviderAccountsPicker(makeCtx({ accountSelected: 'work' }), 'acct_action')!.props.onSelect({ value: 'remove' });
    await flush();
    expect(sent('PATCH', '/credentials/a1b2')?.body).toEqual({ enabled: false });
    expect(sent('DELETE', '/credentials/work')).toBeTruthy();
  });

  it('Check now posts to the row check and refreshes the list', async () => {
    const ctx = makeCtx({ accountSelected: 'env:OPENAI_API_KEY' });
    buildProviderAccountsPicker(ctx, 'acct_action')!.props.onSelect({ value: 'check' });
    await flush();
    expect(sent('POST', '/credentials/env%3AOPENAI_API_KEY/check')).toBeTruthy();
    expect(ctx.setAccountsState).toHaveBeenCalledWith(KEYS);
  });

  it('the order row cycles the strategy', async () => {
    buildProviderAccountsPicker(makeCtx(), 'acct_list')!.props.onSelect({ value: '__strategy__' });
    await flush();
    expect(sent('POST', '/credentials/strategy')?.body).toEqual({ strategy: 'evenly' });
  });

  it('rename patches the label', async () => {
    buildProviderAccountsPicker(makeCtx({ accountSelected: 'a1b2' }), 'acct_rename')!.props.onSubmit('Home');
    await flush();
    expect(sent('PATCH', '/credentials/a1b2')?.body).toEqual({ label: 'Home' });
  });
});
