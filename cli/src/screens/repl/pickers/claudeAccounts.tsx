/**
 * Claude-account pickers — the in-TUI panel for the claude-code provider,
 * mirroring web/components/settings/providers/claude-accounts.tsx. Four
 * picker states:
 *
 *   claude_accounts        — the account list: pick one to manage, or add /
 *                            deactivate from the same menu.
 *   claude_account_action  — activate · deactivate · rename · remove for the
 *                            account picked in the list.
 *   claude_account_add_code — paste the code the browser login printed.
 *   claude_account_rename  — type a new name for the picked account.
 *
 * All mutations go through the worker's REST endpoints (utils/claudeAccounts),
 * the same ones the web UI uses; the underlying proxy is never named. Adding
 * an account auto-installs/starts the backend on first use, opens a browser
 * for the OAuth login, and finishes when the user pastes the code back.
 */
import React from 'react';
import { Picker, PickerItem } from '../../../components/Picker.js';
import { LineInput } from '../../../components/LineInput.js';
import { openInBrowser } from '../../../utils/backend.js';
import {
  fetchAccounts,
  startAdd,
  submitCode,
  useAccount,
  removeAccount,
  renameAccount,
} from '../../../utils/claudeAccounts.js';
import type { PickerCtx } from '../pickerRouter.js';

type ClaudeKind =
  | 'claude_accounts'
  | 'claude_account_action'
  | 'claude_account_add_code'
  | 'claude_account_rename';

const ADD = '__add__';
const DEACTIVATE = '__deactivate__';

export function buildClaudeAccountsPicker(
  ctx: PickerCtx,
  kind: ClaudeKind,
): React.ReactElement | null {
  const {
    pushSystem,
    claudeAccounts, setClaudeAccounts,
    claudeSelected, setClaudeSelected,
    claudePendingAdd, setClaudePendingAdd,
    setPickerKind,
  } = ctx;

  const state = claudeAccounts;
  const active = state.active;

  const refresh = async () => {
    try {
      setClaudeAccounts(await fetchAccounts());
    } catch {
      /* leave the last-known list in place on a transient failure */
    }
  };

  if (kind === 'claude_accounts') {
    const items: PickerItem<string>[] = [];
    for (const a of state.accounts) {
      const tag = a.name === active ? '→ ' : '  ';
      const email = a.email && a.email !== a.name ? `   ${a.email}` : '';
      items.push({
        label: `${tag}${a.name}${email}`,
        description: a.name === active ? 'active' : undefined,
        value: `acct:${a.name}`,
      });
    }
    items.push({
      label: '＋ Add a Claude account',
      description: 'browser login — name defaults to the account email',
      value: ADD,
    });
    if (active) {
      items.push({
        label: '✕ Deactivate — run on no account',
        value: DEACTIVATE,
      });
    }

    const title = active
      ? `Claude accounts — active: ${active}`
      : state.accounts.length
        ? 'Claude accounts — none active'
        : 'Claude accounts — none yet';

    return (
      <Picker
        title={title}
        items={items}
        onSelect={(it) => {
          if (it.value === ADD) {
            pushSystem('Starting Claude login (installing the backend if this is the first time)…');
            void (async () => {
              const r = await startAdd('');
              if (r.error || !r.session) {
                pushSystem(`Could not start the login: ${r.error ?? 'unknown error'}`);
                setPickerKind('claude_accounts');
                return;
              }
              if (r.url) {
                openInBrowser(r.url);
                pushSystem(`Login page: ${r.url}\nSign in with the account you want to add, then paste the code it shows.`);
              }
              setClaudePendingAdd(r);
              setPickerKind('claude_account_add_code');
            })();
            return;
          }
          if (it.value === DEACTIVATE) {
            void (async () => {
              await useAccount('');
              await refresh();
            })();
            return;
          }
          const name = it.value.slice('acct:'.length);
          setClaudeSelected(name);
          setPickerKind('claude_account_action');
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (kind === 'claude_account_action') {
    const sel = claudeSelected ?? '';
    const isActive = sel === active;
    const items: PickerItem<string>[] = [
      isActive
        ? { label: 'Deactivate', description: 'run OpenProgram on no account', value: 'deactivate' }
        : { label: 'Activate', description: 'run OpenProgram on this account', value: 'activate' },
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
            setPickerKind('claude_account_rename');
            return;
          }
          if (it.value === 'back') {
            setPickerKind('claude_accounts');
            return;
          }
          void (async () => {
            if (it.value === 'activate') await useAccount(sel);
            else if (it.value === 'deactivate') await useAccount('');
            else if (it.value === 'remove') {
              await removeAccount(sel);
              pushSystem(`Removed Claude account "${sel}".`);
            }
            await refresh();
            setPickerKind('claude_accounts');
          })();
        }}
        onCancel={() => setPickerKind('claude_accounts')}
      />
    );
  }

  if (kind === 'claude_account_add_code') {
    const url = claudePendingAdd?.url;
    return (
      <LineInput
        label="Paste the code from the Claude login page"
        hint={
          (url ? `Login page: ${url}\n` : '') +
          'Sign in with the account you want to add, then paste the code it shows here.'
        }
        onSubmit={(value) => {
          const code = value.trim();
          if (!claudePendingAdd?.session) {
            pushSystem('This login expired — start "Add a Claude account" again.');
            setClaudePendingAdd(null);
            setPickerKind('claude_accounts');
            return;
          }
          if (!code) {
            pushSystem('Code required.');
            return;
          }
          pushSystem('Finishing login…');
          void (async () => {
            const r = await submitCode(claudePendingAdd.session!, code);
            if (r.ok) {
              pushSystem(`Claude account added${r.name ? `: ${r.name}` : ''}.`);
              setClaudePendingAdd(null);
              await refresh();
              setPickerKind('claude_accounts');
            } else {
              pushSystem(`That code didn't work: ${r.error ?? 'try again'}`);
              if (typeof r.error === 'string' && r.error.includes('no pending')) {
                setClaudePendingAdd(null);
                setPickerKind('claude_accounts');
              }
            }
          })();
        }}
        onCancel={() => {
          setClaudePendingAdd(null);
          setPickerKind('claude_accounts');
        }}
      />
    );
  }

  if (kind === 'claude_account_rename') {
    const sel = claudeSelected ?? '';
    return (
      <LineInput
        label={`Rename "${sel}"`}
        hint="Type a new name for this account."
        initial={sel}
        onSubmit={(value) => {
          const nv = value.trim();
          if (!nv || nv === sel) {
            setPickerKind('claude_account_action');
            return;
          }
          void (async () => {
            const r = await renameAccount(sel, nv);
            if (r.ok) {
              setClaudeSelected(nv);
              await refresh();
              setPickerKind('claude_accounts');
            } else {
              pushSystem(`Rename failed: ${r.error ?? 'unknown error'}`);
            }
          })();
        }}
        onCancel={() => setPickerKind('claude_account_action')}
      />
    );
  }

  return null;
}
