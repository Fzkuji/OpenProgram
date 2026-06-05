import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { buildProviderAccountsPicker } from '../src/screens/repl/pickers/providerAccounts.js';
import type { PickerCtx } from '../src/screens/repl/pickerRouter.js';
import type { AccountsState, AddStarted } from '../src/utils/providerAccounts.js';

// We assert on the returned React element's PROPS rather than rendering it.
// The picker builder returns a <Picker> or <LineInput> with fully-formed
// items / handlers, so prop inspection exercises all the panel's logic
// (which rows, what each selection does) without needing Ink to render —
// ink-testing-library can't resolve `ink` under vitest in this repo.

const flush = () => new Promise((r) => setTimeout(r, 10));

interface Item { label: string; value: string; description?: string }
const items = (el: any): Item[] => el.props.items as Item[];
const labels = (el: any): string[] => items(el).map((i) => i.label);

function makeCtx(over: Partial<PickerCtx> = {}): PickerCtx {
  const base = {
    pushSystem: vi.fn(),
    accountsProviderId: 'claude-code',
    accountsState: {
      installed: true,
      ready: true,
      active: 'work@example.com',
      add_mode: 'code_paste',
      accounts: [
        { name: 'work@example.com', email: 'work@example.com' },
        { name: 'alt', email: 'alt@example.com' },
      ],
    } as AccountsState,
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

// A login-mode provider (no code-paste): drives the shared /login/* flow.
function loginCtx(over: Partial<PickerCtx> = {}): PickerCtx {
  return makeCtx({
    accountsProviderId: 'github-copilot',
    accountsState: {
      installed: true,
      ready: true,
      active: '',
      add_mode: 'login',
      login_methods: [{ id: 'device_code', label: 'Sign in with GitHub' }],
      accounts: [],
    } as AccountsState,
    ...over,
  });
}

describe('Provider accounts panel — list', () => {
  it('renders accounts (active marker + email), Add and Deactivate rows', () => {
    const el = buildProviderAccountsPicker(makeCtx(), 'acct_list')!;
    expect(el.props.title).toContain('active: work@example.com');
    const ls = labels(el);
    expect(ls.some((l) => l.includes('→ work@example.com'))).toBe(true);
    expect(ls.some((l) => l.includes('alt') && l.includes('alt@example.com'))).toBe(true);
    expect(ls.some((l) => l.includes('Add account'))).toBe(true);
    expect(ls.some((l) => l.includes('Deactivate'))).toBe(true);
  });

  it('shows a "none yet" title and no Deactivate when empty', () => {
    const el = buildProviderAccountsPicker(
      makeCtx({ accountsState: { installed: true, ready: true, active: null, add_mode: 'code_paste', accounts: [] } as AccountsState }),
      'acct_list',
    )!;
    expect(el.props.title).toContain('none yet');
    expect(labels(el).some((l) => l.includes('Add account'))).toBe(true);
    expect(labels(el).some((l) => l.includes('Deactivate'))).toBe(false);
  });

  it('selecting an account opens its action menu', () => {
    const ctx = makeCtx();
    const el = buildProviderAccountsPicker(ctx, 'acct_list')!;
    el.props.onSelect({ value: 'acct:alt' });
    expect(ctx.setAccountSelected).toHaveBeenCalledWith('alt');
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_action');
  });
});

describe('Provider accounts panel — action menu', () => {
  it('offers Activate for a non-active account', () => {
    const el = buildProviderAccountsPicker(makeCtx({ accountSelected: 'alt' }), 'acct_action')!;
    expect(el.props.title).toContain('"alt"');
    const ls = labels(el);
    expect(ls).toContain('Activate');
    expect(ls).toContain('Rename');
    expect(ls).toContain('Remove');
    expect(ls.some((l) => l.includes('Back'))).toBe(true);
  });

  it('offers Deactivate for the active account', () => {
    const el = buildProviderAccountsPicker(makeCtx({ accountSelected: 'work@example.com' }), 'acct_action')!;
    expect(labels(el)).toContain('Deactivate');
  });

  it('Rename routes to the rename step (no network)', () => {
    const ctx = makeCtx({ accountSelected: 'alt' });
    const el = buildProviderAccountsPicker(ctx, 'acct_action')!;
    el.props.onSelect({ value: 'rename' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_rename');
  });
});

describe('Provider accounts panel — add-code & rename steps', () => {
  it('add-code step shows the login URL and a paste prompt', () => {
    const el = buildProviderAccountsPicker(
      makeCtx({ accountPendingAdd: { session: 's1', url: 'https://claude.com/oauth/x', name: 'account-1' } }),
      'acct_add_code',
    )!;
    expect(el.props.label).toContain('Paste the code');
    expect(el.props.hint).toContain('https://claude.com/oauth/x');
  });

  it('rename step seeds the input with the current name', () => {
    const el = buildProviderAccountsPicker(makeCtx({ accountSelected: 'alt' }), 'acct_rename')!;
    expect(el.props.label).toContain('Rename "alt"');
    expect(el.props.initial).toBe('alt');
  });
});

describe('Provider accounts panel — login-mode add', () => {
  it('Add account on a login-mode provider goes to the name step', () => {
    const ctx = loginCtx();
    const el = buildProviderAccountsPicker(ctx, 'acct_list')!;
    el.props.onSelect({ value: '__add__' });
    expect(ctx.setAccountLogin).toHaveBeenCalled();
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_login_name');
  });

  it('single-method provider skips the method picker, goes straight to acct_login', () => {
    const ctx = loginCtx();
    const el = buildProviderAccountsPicker(ctx, 'acct_login_name')!;
    el.props.onSubmit('work');
    expect(ctx.setAccountLogin).toHaveBeenCalledWith({ name: 'work', method: 'device_code' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_login');
  });
});

describe('Provider accounts panel — REST wiring', () => {
  let calls: Array<{ url: string; method?: string; body: any }>;
  beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', (url: string, opts?: any) => {
      calls.push({
        url: String(url),
        method: opts?.method,
        body: opts?.body ? JSON.parse(opts.body) : null,
      });
      const u = String(url);
      const body = u.endsWith('/accounts') && opts?.method !== 'POST'
        ? { installed: true, ready: true, active: null, accounts: [] }
        : { ok: true, session: 's1', url: 'https://login', name: 'account-1' };
      return Promise.resolve({ json: () => Promise.resolve(body) } as Response);
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it('Add account (code_paste) POSTs to claude-code /accounts/add then advances to the code step', async () => {
    const ctx = makeCtx();
    const el = buildProviderAccountsPicker(ctx, 'acct_list')!;
    el.props.onSelect({ value: '__add__' });
    await flush();
    expect(calls.some((c) => c.url.includes('/claude-code/accounts/add') && c.method === 'POST')).toBe(true);
    expect(ctx.setAccountPendingAdd).toHaveBeenCalled();
    expect(ctx.setPickerKind).toHaveBeenCalledWith('acct_add_code');
  });

  it('Deactivate row POSTs to /accounts/use with an empty name', async () => {
    const ctx = makeCtx();
    const el = buildProviderAccountsPicker(ctx, 'acct_list')!;
    el.props.onSelect({ value: '__deactivate__' });
    await flush();
    const use = calls.find((c) => c.url.includes('/accounts/use'));
    expect(use?.body).toEqual({ name: '' });
  });

  it('Activate POSTs to /accounts/use with the account name', async () => {
    const ctx = makeCtx({ accountSelected: 'alt' });
    const el = buildProviderAccountsPicker(ctx, 'acct_action')!;
    el.props.onSelect({ value: 'activate' });
    await flush();
    const use = calls.find((c) => c.url.includes('/accounts/use'));
    expect(use?.body).toEqual({ name: 'alt' });
  });

  it('Remove POSTs to /accounts/remove with the account name', async () => {
    const ctx = makeCtx({ accountSelected: 'alt' });
    const el = buildProviderAccountsPicker(ctx, 'acct_action')!;
    el.props.onSelect({ value: 'remove' });
    await flush();
    const rm = calls.find((c) => c.url.includes('/accounts/remove'));
    expect(rm?.body).toEqual({ name: 'alt' });
  });

  it('a generic provider hits ITS OWN /accounts/use path, not claude-code', async () => {
    const ctx = loginCtx({ accountSelected: 'alt', accountsState: {
      installed: true, ready: true, active: 'alt', add_mode: 'login',
      login_methods: [{ id: 'device_code', label: 'Sign in with GitHub' }],
      accounts: [{ name: 'alt' }],
    } as AccountsState });
    const el = buildProviderAccountsPicker(ctx, 'acct_action')!;
    el.props.onSelect({ value: 'activate' });
    await flush();
    const use = calls.find((c) => c.url.includes('/accounts/use'));
    expect(use?.url).toContain('/github-copilot/accounts/use');
  });

  it('submitting the code POSTs session + code to /accounts/add/code', async () => {
    const ctx = makeCtx({ accountPendingAdd: { session: 'sess9', url: 'https://login' } });
    const el = buildProviderAccountsPicker(ctx, 'acct_add_code')!;
    el.props.onSubmit('MYCODE');
    await flush();
    const c = calls.find((x) => x.url.includes('/accounts/add/code'));
    expect(c?.body).toEqual({ session: 'sess9', code: 'MYCODE' });
  });

  it('rename submit POSTs old + new to /accounts/rename', async () => {
    const ctx = makeCtx({ accountSelected: 'alt' });
    const el = buildProviderAccountsPicker(ctx, 'acct_rename')!;
    el.props.onSubmit('renamed');
    await flush();
    const c = calls.find((x) => x.url.includes('/accounts/rename'));
    expect(c?.body).toEqual({ old: 'alt', new: 'renamed' });
  });
});
