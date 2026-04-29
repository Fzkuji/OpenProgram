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
import { Turn } from '../../components/Turn.js';
import { BackendClient } from '../../ws/client.js';
import { tsToDate } from './helpers.js';
import { buildChannelPicker } from './pickers/channel.js';
import { buildRegisterPicker } from './pickers/register.js';
import type {
  AgentInfo,
  ChannelAccountRow,
  PastConversation,
  PendingAttach,
  PickerKind,
  RegisterForm,
  SearchResultRow,
  SessionAliasRow,
} from './types.js';

export interface PickerCtx {
  client: BackendClient;
  pushSystem: (text: string) => void;

  pickerKind: PickerKind;
  pendingAttach: PendingAttach | null;

  chosenChannel: string | undefined;
  chosenAccount: string | undefined;
  conversationId: string | undefined;
  modelsList: string[];
  model: string | undefined;
  agentsList: AgentInfo[];
  channelAccounts: ChannelAccountRow[];
  registerForm: RegisterForm;
  qrAscii: string | undefined;
  qrStatus: string | undefined;
  pastConversations: PastConversation[];
  contextSearchQuery: string;
  searchResults: SearchResultRow[];
  searchBaseDraft: string;

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
  resetScrollbackCursor: () => void;

  sessionAliasesRef: React.MutableRefObject<SessionAliasRow[]>;
}

export function buildPickerNode(ctx: PickerCtx): React.ReactElement | null {
  const {
    client, pushSystem,
    pickerKind, pendingAttach,
    chosenChannel, chosenAccount, conversationId,
    modelsList, model, agentsList, channelAccounts,
    registerForm, qrAscii, qrStatus, pastConversations,
    contextSearchQuery, searchResults, searchBaseDraft,
    setPickerKind, setPendingAttach,
    setChosenChannel, setChosenAccount, setConversationId, setAgent,
    setQrAscii, setQrStatus, setCommitted, setStreaming, setRegisterForm,
    setContextSearchQuery, setSearchResults, setPromptDraft,
    resetScrollbackCursor,
    sessionAliasesRef,
  } = ctx;

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
          resetScrollbackCursor();
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
