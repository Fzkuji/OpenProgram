/**
 * Renders whichever legacy picker is active in REPL.
 *
 * The /channel, /resume, /model, /agent, /theme and channel-binding
 * flows all gate the bottom-row UI through a single `pickerKind`
 * discriminator on REPL state. Each branch wires a Picker (or
 * LineInput, or QR-wait card) and its onSelect to the same set of
 * REPL setters and the WS client. Bundling them into one router
 * keeps REPL.tsx focused on top-level state and effects.
 *
 * `buildPickerNode` is a plain function, NOT a React component:
 *   - It runs once per REPL render — same as the original inline
 *     if/else chain it replaced — so we don't introduce a new mount
 *     boundary or rules-of-hooks frame.
 *   - REPL passes a single `ctx` object so the call site stays a
 *     one-liner; no churn when picker logic adds another setter.
 *
 * Returns null when no picker is active so REPL renders PromptInput
 * in its slot.
 */
import React from 'react';
import { LineInput } from '../../components/LineInput.js';
import { Picker, PickerItem } from '../../components/Picker.js';
import { ThemePicker } from '../../components/ThemePicker.js';
import { SettingsPanel, SettingRow } from '../../components/SettingsPanel.js';
import { SLASH_COMMANDS } from '../../commands/registry.js';
import { Turn } from '../../components/Turn.js';
import { BackendClient } from '../../ws/client.js';
import { tsToDate } from './helpers.js';
import { buildChannelPicker } from './pickers/channel.js';
import { buildRegisterPicker } from './pickers/register.js';
import { buildProviderAccountsPicker, type AccountKind } from './pickers/providerAccounts.js';
import type { AccountsState, AddStarted } from '../../utils/providerAccounts.js';
import type { ColorTheme } from '../../theme/themes.js';
import type {
  AgentInfo,
  BranchRow,
  ChannelAccountRow,
  PastConversation,
  PendingAttach,
  PickerKind,
  RegisterForm,
  SearchResultRow,
  SessionAliasRow,
  ThinkingEffort,
} from './types.js';

const THINKING_EFFORTS: ThinkingEffort[] = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh'];

export interface PickerCtx {
  client: BackendClient;
  colors: ColorTheme;
  pushSystem: (text: string) => void;

  pickerKind: PickerKind;
  pendingAttach: PendingAttach | null;

  chosenChannel: string | undefined;
  chosenAccount: string | undefined;
  conversationId: string | undefined;
  modelsList: string[];
  settingsRows: SettingRow[];
  model: string | undefined;
  agentsList: AgentInfo[];
  channelAccounts: ChannelAccountRow[];
  branchesList: BranchRow[];
  registerForm: RegisterForm;
  qrAscii: string | undefined;
  qrStatus: string | undefined;
  pastConversations: PastConversation[];
  contextSearchQuery: string;
  searchResults: SearchResultRow[];
  searchBaseDraft: string;
  thinkingEffort: ThinkingEffort;

  /** Per-provider account panel state (the in-TUI account manager). The
   *  provider it currently manages, the fetched list, the account picked for the
   *  action menu, the in-flight code-paste add, and the in-flight login-mode add
   *  (the new account's name + chosen sign-in method). */
  accountsProviderId: string;
  accountsState: AccountsState;
  accountSelected: string | null;
  accountPendingAdd: AddStarted | null;
  accountLogin: { name: string; method: string } | null;

  setPickerKind: React.Dispatch<React.SetStateAction<PickerKind>>;
  setPendingAttach: React.Dispatch<React.SetStateAction<PendingAttach | null>>;
  setChosenChannel: React.Dispatch<React.SetStateAction<string | undefined>>;
  setChosenAccount: React.Dispatch<React.SetStateAction<string | undefined>>;
  setConversationId: React.Dispatch<React.SetStateAction<string | undefined>>;
  setAgent: React.Dispatch<React.SetStateAction<string | undefined>>;
  setQrAscii: React.Dispatch<React.SetStateAction<string | undefined>>;
  setQrStatus: React.Dispatch<React.SetStateAction<string | undefined>>;
  setCommitted: React.Dispatch<React.SetStateAction<Turn[]>>;
  setStreaming: React.Dispatch<React.SetStateAction<Turn | null>>;
  setRegisterForm: React.Dispatch<React.SetStateAction<RegisterForm>>;
  setContextSearchQuery: React.Dispatch<React.SetStateAction<string>>;
  setSearchResults: React.Dispatch<React.SetStateAction<SearchResultRow[]>>;
  setPromptDraft: React.Dispatch<React.SetStateAction<string | undefined>>;
  setThinkingEffort: React.Dispatch<React.SetStateAction<ThinkingEffort>>;
  setAccountsProviderId: React.Dispatch<React.SetStateAction<string>>;
  setAccountsState: React.Dispatch<React.SetStateAction<AccountsState>>;
  setAccountSelected: React.Dispatch<React.SetStateAction<string | null>>;
  setAccountPendingAdd: React.Dispatch<React.SetStateAction<AddStarted | null>>;
  setAccountLogin: React.Dispatch<React.SetStateAction<{ name: string; method: string } | null>>;

  /** Run a line as if typed at the prompt (used by the command palette). */
  onSubmit: (text: string) => void;
  sessionAliasesRef: React.MutableRefObject<SessionAliasRow[]>;
}

export function buildPickerNode(ctx: PickerCtx): React.ReactElement | null {
  const {
    client, colors, pushSystem,
    pickerKind, pendingAttach,
    chosenChannel, chosenAccount, conversationId,
    modelsList, settingsRows, model, agentsList, channelAccounts,
    registerForm, qrAscii, qrStatus, pastConversations,
    contextSearchQuery, searchResults, searchBaseDraft, thinkingEffort,
    setPickerKind, setPendingAttach,
    setChosenChannel, setChosenAccount, setConversationId, setAgent,
    setQrAscii, setQrStatus, setCommitted, setStreaming, setRegisterForm,
    setContextSearchQuery, setSearchResults, setPromptDraft, setThinkingEffort,
    onSubmit,
    sessionAliasesRef,
  } = ctx;

  if (pickerKind === 'settings') {
    return (
      <SettingsPanel
        rows={settingsRows}
        actions={[
          { label: 'Model', command: '/model', hint: 'switch model' },
          { label: 'Thinking effort', command: '/effort', hint: 'off … xhigh' },
          { label: 'Theme', command: '/theme', hint: 'live preview' },
          { label: 'Claude accounts', command: '/login', hint: 'add · switch · rename · remove' },
        ]}
        onSet={(key, value) => client.send({ action: 'set_setting', key, value })}
        onRun={(cmd) => onSubmit(cmd)}
        onClose={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'commands') {
    const items: PickerItem<string>[] = SLASH_COMMANDS.map((c) => ({
      label: `/${c.name}`,
      description: c.description,
      value: c.name,
    }));
    return (
      <Picker
        title="Commands"
        items={items}
        onSelect={(it) => {
          setPickerKind(null);
          onSubmit(`/${it.value}`);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'model') {
    const items: PickerItem<string>[] = modelsList.map((m) => ({
      label: m,
      description: m === model ? 'current' : undefined,
      value: m,
    }));
    return (
      <Picker
        title="Switch model"
        items={items}
        onSelect={(it) => {
          client.send({
            action: 'switch_model',
            model: it.value,
            conv_id: conversationId,
          });
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'branch') {
    const items: PickerItem<string>[] = ctx.branchesList.map((b) => ({
      label: b.name + (b.active ? '  (HEAD)' : ''),
      description: b.is_named ? 'named' : undefined,
      value: b.head_msg_id,
    }));
    return (
      <Picker
        title="Switch branch — Enter to checkout"
        items={items}
        onSelect={(it) => {
          if (!conversationId) { setPickerKind(null); return; }
          client.send({
            action: 'checkout_branch',
            session_id: conversationId,
            head_msg_id: it.value,
          });
          // Reload the conversation to repaint with the new HEAD chain.
          client.send({ action: 'load_session', session_id: conversationId });
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'agent') {
    const items: PickerItem<string>[] = agentsList.map((a) => ({
      label: a.name || a.id,
      description: a.default ? `${a.id} · default` : a.id,
      value: a.id,
    }));
    return (
      <Picker
        title="Switch agent"
        items={items}
        onSelect={(it) => {
          client.send({ action: 'set_default_agent', id: it.value });
          setAgent(it.value);
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'effort') {
    const items: PickerItem<ThinkingEffort>[] = THINKING_EFFORTS.map((effort) => ({
      label: effort,
      description: effort === thinkingEffort ? 'current' : undefined,
      value: effort,
    }));
    return (
      <Picker
        title="Set thinking effort"
        items={items}
        onSelect={(it) => {
          setThinkingEffort(it.value);
          pushSystem(`Thinking effort set to ${it.value}.`);
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (
    pickerKind === 'channel' ||
    pickerKind === 'channel_account' ||
    pickerKind === 'channel_action' ||
    pickerKind === 'channel_peer_input' ||
    pickerKind === 'channel_overwrite_confirm' ||
    pickerKind === 'channel_qr_wait'
  ) {
    return buildChannelPicker(ctx, pickerKind);
  }

  if (
    pickerKind === 'register_account_id' ||
    pickerKind === 'register_token'
  ) {
    return buildRegisterPicker(ctx, pickerKind);
  }

  if (
    pickerKind === 'acct_list' ||
    pickerKind === 'acct_action' ||
    pickerKind === 'acct_rename' ||
    pickerKind === 'acct_add_code' ||
    pickerKind === 'acct_login_name' ||
    pickerKind === 'acct_login_method' ||
    pickerKind === 'acct_login'
  ) {
    return buildProviderAccountsPicker(ctx, pickerKind as AccountKind);
  }

  if (pickerKind === 'context_search') {
    return (
      <LineInput
        label="Search past context"
        hint="Search saved session messages. Pick a result to paste it into the prompt."
        initial={contextSearchQuery}
        onSubmit={(value) => {
          const query = value.trim();
          if (!query) {
            pushSystem('Search query required.');
            return;
          }
          setContextSearchQuery(query);
          setSearchResults([]);
          if (searchBaseDraft) setPromptDraft(searchBaseDraft);
          setPickerKind(null);
          client.send({ action: 'search_messages', query, limit: 50 });
        }}
        onCancel={() => {
          if (searchBaseDraft) setPromptDraft(searchBaseDraft);
          setPickerKind(null);
        }}
      />
    );
  }

  if (pickerKind === 'context_search_results') {
    const items: PickerItem<SearchResultRow>[] = searchResults.map((r) => {
      const source = r.session_source ? `[${r.session_source}] ` : '';
      const title = r.session_title || r.session_id || 'session';
      const role = r.role ? `[${r.role}]` : '[message]';
      return {
        label: `${role} ${source}${title}`.slice(0, 60),
        description: r.preview,
        value: r,
      };
    });
    return (
      <Picker
        title={`Search "${contextSearchQuery}"`}
        items={items}
        onSelect={(it) => {
          const text = (it.value.content || it.value.preview || '').trim();
          if (text) {
            setPromptDraft(searchBaseDraft.trim()
              ? `${searchBaseDraft.trimEnd()}\n\n${text}`
              : text);
          } else if (searchBaseDraft) {
            setPromptDraft(searchBaseDraft);
          }
          setPickerKind(null);
        }}
        onCancel={() => {
          if (searchBaseDraft) setPromptDraft(searchBaseDraft);
          setPickerKind(null);
        }}
      />
    );
  }

  if (pickerKind === 'theme') {
    return (
      <ThemePicker
        onDone={(setting) => {
          setPickerKind(null);
          pushSystem(`Theme set to ${setting}.`);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  if (pickerKind === 'resume') {
    // Channel-bound sessions (source="wechat"/"telegram"/…) bubble
    // to the top with a [channel:peer] tag prefix so users can pick
    // a wechat conversation directly without scanning random IDs.
    const sorted = [...pastConversations].sort((a, b) => {
      const aChan = a.source ? 0 : 1;
      const bChan = b.source ? 0 : 1;
      if (aChan !== bChan) return aChan - bChan;
      return (b.created_at ?? 0) - (a.created_at ?? 0);
    });
    const items: PickerItem<string>[] = sorted
      .filter((c) => c.id)
      .map((c) => {
        const tag = c.source
          ? `[${c.source}${c.peer_display ? `:${c.peer_display}` : ''}] `
          : '';
        const title = c.title || c.id || '';
        return {
          label: (tag + title).slice(0, 60),
          description: `${c.id ?? ''} · ${tsToDate(c.created_at)}`,
          value: c.id!,
        };
      });
    return (
      <Picker
        title="Resume a session"
        items={items}
        onSelect={(it) => {
          client.send({ action: 'load_conversation', conv_id: it.value });
          setConversationId(it.value);
          setCommitted([]);
          setStreaming(null);
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  return null;
}
