/** A provider's credentials in the TUI: the same ordered list of keys and
 *  signed-in accounts as Settings → LLM Providers → Credentials, through the
 *  shared /credentials and /login endpoints. */
import React from 'react';
import { Picker, PickerItem } from '../../../components/Picker.js';
import { LineInput } from '../../../components/LineInput.js';
import { addMethods, isKey, makeCredentialsClient, statusWord } from '../../../utils/providerAccounts.js';
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
const STRATEGY = '__strategy__';

const STRATEGY_LABEL: Record<string, string> = {
  in_order: 'in order',
  evenly: 'evenly',
  least_used: 'least used first',
};

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

  const client = makeCredentialsClient(provider);
  const rows = state.credentials;
  const methods = addMethods(state);
  const signIns = rows.length > 0 && rows.every((row) => !isKey(row));
  const noun = signIns ? 'account' : 'key';

  const refresh = async () => {
    try {
      setAccountsState(await client.fetch());
    } catch {
      /* leave the last-known list in place on a transient failure */
    }
  };

  const methodLabel = (id: string): string =>
    methods.find((m) => m.id === id)?.label ?? `Sign in (${id})`;

  if (kind === 'acct_list') {
    const items: PickerItem<string>[] = rows.map((row) => {
      const tail = row.env_var ? `$${row.env_var}` : row.key_tail ? `····${row.key_tail}` : row.identity.plan ?? '';
      return {
        label: `${row.id === state.active ? '→ ' : '  '}${row.display_label}${tail ? `   ${tail}` : ''}`,
        description: row.id === state.active ? `in use · ${statusWord(row)}` : statusWord(row),
        value: `acct:${row.id}`,
      };
    });
    if (rows.length > 1 && !signIns && state.strategies.length) {
      items.push({
        label: `⟳ Order: ${STRATEGY_LABEL[state.strategy] ?? state.strategy}`,
        description: 'choose how requests spread across the keys',
        value: STRATEGY,
      });
    }
    items.push({
      label: signIns ? '＋ Sign in with another account' : `＋ Add ${noun}`,
      description: methods.length > 1 ? 'paste a key or sign in' : methods[0]?.label,
      value: ADD,
    });

    const title = rows.length
      ? `${provider} credentials — used ${STRATEGY_LABEL[state.strategy] ?? 'in order'}`
      : `${provider} credentials — none yet`;

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
          if (it.value === STRATEGY) {
            const all = state.strategies;
            const next = all[(all.indexOf(state.strategy) + 1) % all.length];
            void (async () => {
              await client.setStrategy(next);
              await refresh();
              pushSystem(`${provider} credentials are now used ${STRATEGY_LABEL[next] ?? next}.`);
            })();
            return;
          }
          setAccountSelected(it.value.slice('acct:'.length));
          setPickerKind('acct_action');
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (kind === 'acct_action') {
    const sel = accountSelected ?? '';
    const index = rows.findIndex((row) => row.id === sel);
    const row = rows[index];
    const name = row?.display_label ?? sel;
    const items: PickerItem<string>[] = [
      { label: 'Check now', description: 'ask the provider whether it works', value: 'check' },
      ...(row?.env_var ? [] : [{ label: 'Rename', value: 'rename' }]),
      row && !row.enabled
        ? { label: 'Use again', value: 'enable' }
        : { label: "Don't use for now", description: 'keep it, but skip it until turned back on', value: 'disable' },
      ...(index > 0 ? [{ label: 'Move up', value: 'up' }] : []),
      ...(index >= 0 && index < rows.length - 1 ? [{ label: 'Move down', value: 'down' }] : []),
      ...(row?.env_var ? [] : [{ label: 'Remove', description: 'delete it from this machine', value: 'remove' }]),
      { label: '← Back', value: 'back' },
    ];
    return (
      <Picker
        title={`${row && isKey(row) ? 'Key' : 'Account'} "${name}"`}
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
          if (it.value === 'check') {
            pushSystem(`Checking "${name}"…`);
            void (async () => {
              const r = await client.check(sel);
              pushSystem(r.error
                ? `Check failed: ${r.error}`
                : `"${name}": ${r.check?.result ?? 'checked'}${r.check?.message ? ` — ${r.check.message}` : ''}`);
              await refresh();
            })();
            return;
          }
          void (async () => {
            let r: { error?: string } = {};
            if (it.value === 'enable' || it.value === 'disable') r = await client.setEnabled(sel, it.value === 'enable');
            else if (it.value === 'up' || it.value === 'down') {
              const order = rows.map((x) => x.id);
              const to = it.value === 'up' ? index - 1 : index + 1;
              [order[index], order[to]] = [order[to], order[index]];
              r = await client.reorder(order);
            } else if (it.value === 'remove') {
              r = await client.remove(sel);
              if (!r.error) pushSystem(`Removed "${name}".`);
            }
            if (r.error) pushSystem(r.error);
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
    const currentLabel = rows.find((row) => row.id === sel)?.display_label ?? sel;
    return (
      <LineInput
        label={`Rename "${currentLabel}"`}
        hint="Type a new name."
        initial={currentLabel}
        onSubmit={(value) => {
          const nv = value.trim();
          if (!nv || nv === currentLabel) {
            setPickerKind('acct_action');
            return;
          }
          void (async () => {
            const r = await client.rename(sel, nv);
            if (!r.error) {
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
        label={`Add a ${provider} ${noun}`}
        hint="Optional name (e.g. Work). Leave blank to name it by its account or last four characters."
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
        title={`Add to ${provider}`}
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
