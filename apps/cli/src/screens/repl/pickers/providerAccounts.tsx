/** Provider account management through the shared account and login endpoints. */
import React from 'react';
import { Picker, PickerItem } from '../../../components/Picker.js';
import { LineInput } from '../../../components/LineInput.js';
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

const stableId = (account: { id?: string; name: string }): string => account.id || account.name;
const displayLabel = (account: { label?: string; name: string }): string => account.label || account.name;

export function buildProviderAccountsPicker(
  ctx: PickerCtx,
  kind: AccountKind,
): React.ReactElement | null {
  const {
    pushSystem,
    accountsProviderId: provider,
    accountsState: state, setAccountsState,
    accountSelected, setAccountSelected,
    accountLogin, setAccountLogin,
    setPickerKind,
  } = ctx;

  const client = makeAccountsClient(provider);
  const active = state.active;
  const activeAccount = state.accounts.find((account) => stableId(account) === active);
  const activeLabel = activeAccount ? displayLabel(activeAccount) : active;
  const methods = state.login_methods ?? [];

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
      const id = stableId(a);
      const label = displayLabel(a);
      const tag = id === active ? '→ ' : '  ';
      const email = a.email && a.email !== label ? `   ${a.email}` : '';
      const keys = a.count && a.count > 1 ? `   (${a.count} keys)` : '';
      items.push({
        label: `${tag}${label}${email}${keys}`,
        description: id === active ? 'active' : undefined,
        value: `acct:${id}`,
      });
    }
    if (state.accounts.length > 1) {
      items.push({
        label: `⟳ Rotation: ${state.rotation ? 'on' : 'off'}`,
        description: state.rotation ? `rotating across accounts (${state.strategy ?? 'fill_first'})` : 'use the active account only',
        value: ROTATE,
      });
    }
    items.push({
      label: '＋ Add account',
      description: 'sign in to add another account',
      value: ADD,
    });
    if (active) {
      items.push({
        label: '✕ Deactivate — fall back to the default account',
        value: DEACTIVATE,
      });
    }

    const title = active
      ? `${provider} accounts — active: ${activeLabel}`
      : state.accounts.length
        ? `${provider} accounts — none active`
        : `${provider} accounts — none yet`;

    return (
      <Picker
        title={title}
        items={items}
        onSelect={(it) => {
          if (it.value === ADD) {
            setAccountLogin({ name: '', method: methods[0]?.id ?? 'api_key' });
            setPickerKind('acct_login_name');
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
    const selectedLabel = displayLabel(
      state.accounts.find((account) => stableId(account) === sel) ?? { name: sel },
    );
    const isActive = sel === active;
    const items: PickerItem<string>[] = [
      isActive
        ? { label: 'Deactivate', description: 'fall back to the default account', value: 'deactivate' }
        : { label: 'Activate', description: 'run this provider on this account', value: 'activate' },
      { label: 'Validate', description: 'check this account against the provider', value: 'validate' },
      { label: 'Reveal key', description: 'print the full API key', value: 'reveal' },
      { label: 'Rename', value: 'rename' },
      { label: 'Remove', value: 'remove' },
      { label: '← Back', value: 'back' },
    ];
    return (
      <Picker
        title={`Account "${selectedLabel}"`}
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
              pushSystem(r.ok ? `"${selectedLabel}": ${r.status}${r.detail ? ` — ${r.detail}` : ''}` : `Validate failed: ${r.error ?? 'unknown'}`);
            })();
            return;
          }
          if (it.value === 'reveal') {
            void (async () => {
              const r = await client.revealKey(sel);
              pushSystem(r.ok ? `"${selectedLabel}" key:\n${r.value}` : (r.error ?? 'no key on this account'));
            })();
            return;
          }
          void (async () => {
            if (it.value === 'activate') await client.useAccount(sel);
            else if (it.value === 'deactivate') await client.useAccount('');
            else if (it.value === 'remove') {
              await client.removeAccount(sel);
              pushSystem(`Removed account "${selectedLabel}".`);
            }
            await refresh();
            setPickerKind('acct_list');
          })();
        }}
        onCancel={() => setPickerKind('acct_list')}
      />
    );
  }

  if (kind === 'acct_rename') {
    const sel = accountSelected ?? '';
    const currentLabel = displayLabel(
      state.accounts.find((account) => stableId(account) === sel) ?? { name: sel },
    );
    return (
      <LineInput
        label={`Rename "${currentLabel}"`}
        hint="Type a new name for this account."
        initial={currentLabel}
        onSubmit={(value) => {
          const nv = value.trim();
          if (!nv || nv === currentLabel) {
            setPickerKind('acct_action');
            return;
          }
          void (async () => {
            const r = await client.renameAccount(sel, nv);
            if (r.ok) {
              setAccountSelected(sel);
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
        hint="Optional label (e.g. Work). Leave blank to use the signed-in identity."
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
        accountLabel={name}
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
