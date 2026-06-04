import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { buildClaudeAccountsPicker } from '../src/screens/repl/pickers/claudeAccounts.js';
import type { PickerCtx } from '../src/screens/repl/pickerRouter.js';
import type { ClaudeAccountsState, AddStarted } from '../src/utils/claudeAccounts.js';

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
    claudeAccounts: {
      installed: true,
      ready: true,
      active: 'work@example.com',
      accounts: [
        { name: 'work@example.com', email: 'work@example.com' },
        { name: 'alt', email: 'alt@example.com' },
      ],
    } as ClaudeAccountsState,
    claudeSelected: null as string | null,
    claudePendingAdd: null as AddStarted | null,
    setClaudeAccounts: vi.fn(),
    setClaudeSelected: vi.fn(),
    setClaudePendingAdd: vi.fn(),
    setPickerKind: vi.fn(),
  };
  return { ...base, ...over } as unknown as PickerCtx;
}

describe('Claude accounts panel — list', () => {
  it('renders accounts (active marker + email), Add and Deactivate rows', () => {
    const el = buildClaudeAccountsPicker(makeCtx(), 'claude_accounts')!;
    expect(el.props.title).toContain('active: work@example.com');
    const ls = labels(el);
    expect(ls.some((l) => l.includes('→ work@example.com'))).toBe(true);
    expect(ls.some((l) => l.includes('alt') && l.includes('alt@example.com'))).toBe(true);
    expect(ls.some((l) => l.includes('Add a Claude account'))).toBe(true);
    expect(ls.some((l) => l.includes('Deactivate'))).toBe(true);
  });

  it('shows a "none yet" title and no Deactivate when empty', () => {
    const el = buildClaudeAccountsPicker(
      makeCtx({ claudeAccounts: { installed: true, ready: true, active: null, accounts: [] } as ClaudeAccountsState }),
      'claude_accounts',
    )!;
    expect(el.props.title).toContain('none yet');
    expect(labels(el).some((l) => l.includes('Add a Claude account'))).toBe(true);
    expect(labels(el).some((l) => l.includes('Deactivate'))).toBe(false);
  });

  it('selecting an account opens its action menu', () => {
    const ctx = makeCtx();
    const el = buildClaudeAccountsPicker(ctx, 'claude_accounts')!;
    el.props.onSelect({ value: 'acct:alt' });
    expect(ctx.setClaudeSelected).toHaveBeenCalledWith('alt');
    expect(ctx.setPickerKind).toHaveBeenCalledWith('claude_account_action');
  });
});

describe('Claude accounts panel — action menu', () => {
  it('offers Activate for a non-active account', () => {
    const el = buildClaudeAccountsPicker(makeCtx({ claudeSelected: 'alt' }), 'claude_account_action')!;
    expect(el.props.title).toContain('"alt"');
    const ls = labels(el);
    expect(ls).toContain('Activate');
    expect(ls).toContain('Rename');
    expect(ls).toContain('Remove');
    expect(ls.some((l) => l.includes('Back'))).toBe(true);
  });

  it('offers Deactivate for the active account', () => {
    const el = buildClaudeAccountsPicker(makeCtx({ claudeSelected: 'work@example.com' }), 'claude_account_action')!;
    expect(labels(el)).toContain('Deactivate');
  });

  it('Rename routes to the rename step (no network)', () => {
    const ctx = makeCtx({ claudeSelected: 'alt' });
    const el = buildClaudeAccountsPicker(ctx, 'claude_account_action')!;
    el.props.onSelect({ value: 'rename' });
    expect(ctx.setPickerKind).toHaveBeenCalledWith('claude_account_rename');
  });
});

describe('Claude accounts panel — add-code & rename steps', () => {
  it('add-code step shows the login URL and a paste prompt', () => {
    const el = buildClaudeAccountsPicker(
      makeCtx({ claudePendingAdd: { session: 's1', url: 'https://claude.com/oauth/x', name: 'account-1' } }),
      'claude_account_add_code',
    )!;
    expect(el.props.label).toContain('Paste the code');
    expect(el.props.hint).toContain('https://claude.com/oauth/x');
  });

  it('rename step seeds the input with the current name', () => {
    const el = buildClaudeAccountsPicker(makeCtx({ claudeSelected: 'alt' }), 'claude_account_rename')!;
    expect(el.props.label).toContain('Rename "alt"');
    expect(el.props.initial).toBe('alt');
  });
});

describe('Claude accounts panel — REST wiring', () => {
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

  it('Add account POSTs to /accounts/add then advances to the code step', async () => {
    const ctx = makeCtx();
    const el = buildClaudeAccountsPicker(ctx, 'claude_accounts')!;
    el.props.onSelect({ value: '__add__' });
    await flush();
    expect(calls.some((c) => c.url.includes('/accounts/add') && c.method === 'POST')).toBe(true);
    expect(ctx.setClaudePendingAdd).toHaveBeenCalled();
    expect(ctx.setPickerKind).toHaveBeenCalledWith('claude_account_add_code');
  });

  it('Deactivate row POSTs to /accounts/use with an empty name', async () => {
    const ctx = makeCtx();
    const el = buildClaudeAccountsPicker(ctx, 'claude_accounts')!;
    el.props.onSelect({ value: '__deactivate__' });
    await flush();
    const use = calls.find((c) => c.url.includes('/accounts/use'));
    expect(use?.body).toEqual({ name: '' });
  });

  it('Activate POSTs to /accounts/use with the account name', async () => {
    const ctx = makeCtx({ claudeSelected: 'alt' });
    const el = buildClaudeAccountsPicker(ctx, 'claude_account_action')!;
    el.props.onSelect({ value: 'activate' });
    await flush();
    const use = calls.find((c) => c.url.includes('/accounts/use'));
    expect(use?.body).toEqual({ name: 'alt' });
  });

  it('Remove POSTs to /accounts/remove with the account name', async () => {
    const ctx = makeCtx({ claudeSelected: 'alt' });
    const el = buildClaudeAccountsPicker(ctx, 'claude_account_action')!;
    el.props.onSelect({ value: 'remove' });
    await flush();
    const rm = calls.find((c) => c.url.includes('/accounts/remove'));
    expect(rm?.body).toEqual({ name: 'alt' });
  });

  it('submitting the code POSTs session + code to /accounts/add/code', async () => {
    const ctx = makeCtx({ claudePendingAdd: { session: 'sess9', url: 'https://login' } });
    const el = buildClaudeAccountsPicker(ctx, 'claude_account_add_code')!;
    el.props.onSubmit('MYCODE');
    await flush();
    const c = calls.find((x) => x.url.includes('/accounts/add/code'));
    expect(c?.body).toEqual({ session: 'sess9', code: 'MYCODE' });
  });

  it('rename submit POSTs old + new to /accounts/rename', async () => {
    const ctx = makeCtx({ claudeSelected: 'alt' });
    const el = buildClaudeAccountsPicker(ctx, 'claude_account_rename')!;
    el.props.onSubmit('renamed');
    await flush();
    const c = calls.find((x) => x.url.includes('/accounts/rename'));
    expect(c?.body).toEqual({ old: 'alt', new: 'renamed' });
  });
});
