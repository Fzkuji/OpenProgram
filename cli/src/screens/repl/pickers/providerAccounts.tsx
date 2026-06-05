/**
 * Provider-account pickers — the in-TUI account manager for ANY provider,
 * mirroring web/components/settings/providers/provider-accounts.tsx. One picker
 * set, parameterized by ctx.accountsProviderId; claude-code is just one
 * instance. Picker states:
 *
 *   acct_list          — the account list: pick one to manage, add, or deactivate.
 *   acct_action        — activate · deactivate · rename · remove for the picked one.
 *   acct_rename        — type a new name for the picked account.
 *   acct_add_code      — (code_paste add) paste the code the browser login printed.
 *   acct_login_name    — (login add) name the new account.
 *   acct_login_method  — (login add) pick a sign-in method when there's >1.
 *   acct_login         — (login add) run the shared /login flow to completion.
 *
 * "Add account" branches on the backend's add_mode: claude-code uses the
 * interactive code-paste pair; every other provider uses the shared /login/*
 * flow (ProviderLoginFlow). All other ops are identical across backends. The
 * underlying proxy for claude-code is never named here.
 */
import React from 'react';
import { Picker, PickerItem } from '../../../components/Picker.js';
import { LineInput } from '../../../components/LineInput.js';
import { openInBrowser } from '../../../utils/backend.js';
import { makeAccountsClient } from '../../../utils/providerAccounts.js';
import { ProviderLoginFlow } from './providerLoginFlow.js';
import type { PickerCtx } from '../pickerRouter.js';

export type AccountKind =
  | 'acct_list'
  | 'acct_action'
  | 'acct_rename'
  | 'acct_add_code'
  | 'acct_login_name'
  | 'acct_login_method'
  | 'acct_login';

const ADD = '__add__';
const DEACTIVATE = '__deactivate__';
const ROTATE = '__rotate__';

export function buildProviderAccountsPicker(
  ctx: PickerCtx,
  kind: AccountKind,
): React.ReactElement | null {
  const {
    pushSystem,
    accountsProviderId: provider,
    accountsState: state, setAccountsState,
    accountSelected, setAccountSelected,
    accountPendingAdd, setAccountPendingAdd,
    accountLogin, setAccountLogin,
    setPickerKind,
  } = ctx;

  const client = makeAccountsClient(provider);
  const active = state.active;
  const methods = state.login_methods ?? [];
  const codePaste = state.add_mode === 'code_paste';

  const refresh = async () => {
    try {
      setAccountsState(await client.fetchAccounts());
    } catch {
      /* leave the last-known list in place on a transient failure */
    }
  };

  const methodLabel = (id: string): string =>
    methods.find((m) => m.id === id)?.label ?? `Sign in (${id})`;

  if (kind === 'acct_list') {
    const items: PickerItem<string>[] = [];
    for (const a of state.accounts) {
      const tag = a.name === active ? '→ ' : '  ';
      const email = a.email && a.email !== a.name ? `   ${a.email}` : '';
      const keys = a.count && a.count > 1 ? `   (${a.count} keys)` : '';
      items.push({
        label: `${tag}${a.name}${email}${keys}`,
        description: a.name === active ? 'active' : undefined,
        value: `acct:${a.name}`,
      });
    }
    if (!codePaste && state.accounts.length > 1) {
      items.push({
        label: `⟳ Rotation: ${state.rotation ? 'on' : 'off'}`,
        description: state.rotation ? `rotating across accounts (${state.strategy ?? 'fill_first'})` : 'use the active account only',
        value: ROTATE,
      });
    }
    items.push({
      label: '＋ Add account',
      description: codePaste ? 'browser login — name defaults to the account email' : 'sign in to add another account',
      value: ADD,
    });
    if (active) {
      items.push({
        label: '✕ Deactivate — fall back to the default account',
        value: DEACTIVATE,
      });
    }

    const title = active
      ? `${provider} accounts — active: ${active}`
      : state.accounts.length
        ? `${provider} accounts — none active`
        : `${provider} accounts — none yet`;

    return (
      <Picker
        title={title}
        items={items}
        onSelect={(it) => {
          if (it.value === ADD) {
            if (codePaste) {
              pushSystem('Starting login (installing the backend if this is the first time)…');
              void (async () => {
                const r = await client.startAdd('');
                if (r.error || !r.session) {
                  pushSystem(`Could not start the login: ${r.error ?? 'unknown error'}`);
                  setPickerKind('acct_list');
                  return;
                }
                if (r.url) {
                  openInBrowser(r.url);
                  pushSystem(`Login page: ${r.url}\nSign in with the account you want to add, then paste the code it shows.`);
                }
                setAccountPendingAdd(r);
                setPickerKind('acct_add_code');
              })();
            } else {
              // login-mode add: name → (method) → run the shared flow.
              setAccountLogin({ name: '', method: methods[0]?.id ?? 'api_key' });
              setPickerKind('acct_login_name');
            }
            return;
          }
          if (it.value === DEACTIVATE) {
            void (async () => {
              await client.useAccount('');
              await refresh();
            })();
            return;
          }
          if (it.value === ROTATE) {
            void (async () => {
              await client.setRotation(!state.rotation, state.strategy);
              await refresh();
              pushSystem(`Rotation ${!state.rotation ? 'on' : 'off'} for ${provider}.`);
            })();
            return;
          }
          const name = it.value.slice('acct:'.length);
          setAccountSelected(name);
          setPickerKind('acct_action');
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (kind === 'acct_action') {
    const sel = accountSelected ?? '';
    const isActive = sel === active;
    const items: PickerItem<string>[] = [
      isActive
        ? { label: 'Deactivate', description: 'fall back to the default account', value: 'deactivate' }
        : { label: 'Activate', description: 'run this provider on this account', value: 'activate' },
      { label: 'Validate', description: 'check this account against the provider', value: 'validate' },
      ...(codePaste ? [] : [{ label: 'Reveal key', description: 'print the full API key', value: 'reveal' }]),
      { label: 'Rename', value: 'rename' },
      { label: 'Remove', value: 'remove' },
      { label: '← Back', value: 'back' },
    ];
    return (
      <Picker
        title={`Account "${sel}"`}
        items={items}
        onSelect={(it) => {
          if (it.value === 'rename') {
            setPickerKind('acct_rename');
            return;
          }
          if (it.value === 'back') {
            setPickerKind('acct_list');
            return;
          }
          if (it.value === 'validate') {
            pushSystem(`Validating "${sel}"…`);
            void (async () => {
              const r = await client.validateAccount(sel);
              pushSystem(r.ok ? `"${sel}": ${r.status}${r.detail ? ` — ${r.detail}` : ''}` : `Validate failed: ${r.error ?? 'unknown'}`);
            })();
            return;
          }
          if (it.value === 'reveal') {
            void (async () => {
              const r = await client.revealKey(sel);
              pushSystem(r.ok ? `"${sel}" key:\n${r.value}` : (r.error ?? 'no key on this account'));
            })();
            return;
          }
          void (async () => {
            if (it.value === 'activate') await client.useAccount(sel);
            else if (it.value === 'deactivate') await client.useAccount('');
            else if (it.value === 'remove') {
              await client.removeAccount(sel);
              pushSystem(`Removed account "${sel}".`);
            }
            await refresh();
            setPickerKind('acct_list');
          })();
        }}
        onCancel={() => setPickerKind('acct_list')}
      />
    );
  }

  if (kind === 'acct_add_code') {
    const url = accountPendingAdd?.url;
    return (
      <LineInput
        label="Paste the code from the login page"
        hint={
          (url ? `Login page: ${url}\n` : '') +
          'Sign in with the account you want to add, then paste the code it shows here.'
        }
        onSubmit={(value) => {
          const code = value.trim();
          if (!accountPendingAdd?.session) {
            pushSystem('This login expired — start "Add account" again.');
            setAccountPendingAdd(null);
            setPickerKind('acct_list');
            return;
          }
          if (!code) {
            pushSystem('Code required.');
            return;
          }
          pushSystem('Finishing login…');
          void (async () => {
            const r = await client.submitCode(accountPendingAdd.session!, code);
            if (r.ok) {
              pushSystem(`Account added${r.name ? `: ${r.name}` : ''}.`);
              setAccountPendingAdd(null);
              await refresh();
              setPickerKind('acct_list');
            } else {
              pushSystem(`That code didn't work: ${r.error ?? 'try again'}`);
              if (typeof r.error === 'string' && r.error.includes('no pending')) {
                setAccountPendingAdd(null);
                setPickerKind('acct_list');
              }
            }
          })();
        }}
        onCancel={() => {
          setAccountPendingAdd(null);
          setPickerKind('acct_list');
        }}
      />
    );
  }

  if (kind === 'acct_rename') {
    const sel = accountSelected ?? '';
    return (
      <LineInput
        label={`Rename "${sel}"`}
        hint="Type a new name for this account."
        initial={sel}
        onSubmit={(value) => {
          const nv = value.trim();
          if (!nv || nv === sel) {
            setPickerKind('acct_action');
            return;
          }
          void (async () => {
            const r = await client.renameAccount(sel, nv);
            if (r.ok) {
              setAccountSelected(nv);
              await refresh();
              setPickerKind('acct_list');
            } else {
              pushSystem(`Rename failed: ${r.error ?? 'unknown error'}`);
            }
          })();
        }}
        onCancel={() => setPickerKind('acct_action')}
      />
    );
  }

  if (kind === 'acct_login_name') {
    return (
      <LineInput
        label={`Add a ${provider} account`}
        hint="Name this account (e.g. work). Leave blank to fill the default account."
        onSubmit={(value) => {
          const name = value.trim();
          const m0 = methods[0]?.id ?? 'api_key';
          setAccountLogin({ name, method: m0 });
          setPickerKind(methods.length > 1 ? 'acct_login_method' : 'acct_login');
        }}
        onCancel={() => {
          setAccountLogin(null);
          setPickerKind('acct_list');
        }}
      />
    );
  }

  if (kind === 'acct_login_method') {
    const items: PickerItem<string>[] = methods.map((m) => ({
      label: m.label,
      value: m.id,
    }));
    return (
      <Picker
        title={`Sign in to ${provider}`}
        items={items}
        onSelect={(it) => {
          setAccountLogin({ name: accountLogin?.name ?? '', method: it.value });
          setPickerKind('acct_login');
        }}
        onCancel={() => {
          setAccountLogin(null);
          setPickerKind('acct_list');
        }}
      />
    );
  }

  if (kind === 'acct_login') {
    const name = accountLogin?.name ?? '';
    const method = accountLogin?.method ?? methods[0]?.id ?? 'api_key';
    return (
      <ProviderLoginFlow
        providerId={provider}
        profileId={name || 'default'}
        method={method}
        label={methodLabel(method)}
        onDone={({ message }) => {
          pushSystem(message);
          setAccountLogin(null);
          void refresh();
          setPickerKind('acct_list');
        }}
        onCancel={() => {
          setAccountLogin(null);
          setPickerKind('acct_list');
        }}
      />
    );
  }

  return null;
}
