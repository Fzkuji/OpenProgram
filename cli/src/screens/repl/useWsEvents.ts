/**
 * useWsEvents — registers the WS message handler for REPL.
 *
 * Latest Ref Pattern: effect re-registers only on client change.
 * All ctx fields are read via ctxRef so the handler always sees
 * current state without needing exhaustive-deps.
 */
import { useEffect, useRef } from 'react';
import {
  BackendClient,
  WsEnvelope,
  StatsEnvelope,
  ConnectionState,
} from '../../ws/client.js';
import { Turn, TurnBlock } from '../../components/Turn.js';
import { trimHistoryFile } from '../../utils/history.js';
import { randomLocalId, renderModel, stripProviderPrefix } from './helpers.js';
import {
  upsertStreamingText,
  appendStreamingTool,
  finalizeStreamingTools,
} from './wsHandlers/streamingHelpers.js';
import type {
  Activity,
  AgentInfo,
  ChannelAccountRow,
  PastConversation,
  PendingAttach,
  PickerKind,
  SearchResultRow,
  SessionAliasRow,
  ThinkingEffort,
} from './types.js';

export interface WsEventsCtx {
  client: BackendClient;

  // helpers reused by callers outside the WS handler
  pushSystem: (text: string) => void;
  finishTurn: () => void;

  // state read inside dispatch (always latest via ref pattern)
  bellEnabled: boolean;
  conversationId: string | undefined;
  chosenChannel: string | undefined;
  chosenAccount: string | undefined;

  // setters
  setConversationId: React.Dispatch<React.SetStateAction<string | undefined>>;
  setStreaming: React.Dispatch<React.SetStateAction<Turn | null>>;
  setActivity: React.Dispatch<React.SetStateAction<Activity | null>>;
  setCommitted: React.Dispatch<React.SetStateAction<Turn[]>>;
  setTokensByConv: React.Dispatch<React.SetStateAction<Record<string, { input?: number; output?: number }>>>;
  setWindowByConv: React.Dispatch<React.SetStateAction<Record<string, number>>>;
  setStats: React.Dispatch<React.SetStateAction<StatsEnvelope['data'] | undefined>>;
  setModel: React.Dispatch<React.SetStateAction<string | undefined>>;
  setAgent: React.Dispatch<React.SetStateAction<string | undefined>>;
  setAgentsList: React.Dispatch<React.SetStateAction<AgentInfo[]>>;
  setModelsList: React.Dispatch<React.SetStateAction<string[]>>;
  setChannelAccounts: React.Dispatch<React.SetStateAction<ChannelAccountRow[]>>;
  setPastConversations: React.Dispatch<React.SetStateAction<PastConversation[]>>;
  setQrAscii: React.Dispatch<React.SetStateAction<string | undefined>>;
  setQrStatus: React.Dispatch<React.SetStateAction<string | undefined>>;
  setPickerKind: React.Dispatch<React.SetStateAction<PickerKind>>;
  setChosenChannel: React.Dispatch<React.SetStateAction<string | undefined>>;
  setChosenAccount: React.Dispatch<React.SetStateAction<string | undefined>>;
  setConversationTitle: React.Dispatch<React.SetStateAction<string | undefined>>;
  setConnState: React.Dispatch<React.SetStateAction<ConnectionState>>;
  setToolsOn: React.Dispatch<React.SetStateAction<boolean>>;
  setThinkingEffort: React.Dispatch<React.SetStateAction<ThinkingEffort>>;
  setPermissionMode: React.Dispatch<React.SetStateAction<'ask' | 'auto' | 'bypass'>>;
  setSearchResults: React.Dispatch<React.SetStateAction<SearchResultRow[]>>;
  setContextSearchQuery: React.Dispatch<React.SetStateAction<string>>;
  setSessionLiveByConv: React.Dispatch<React.SetStateAction<Record<string, boolean>>>;

  // refs (object identity stable, no need for ref pattern)
  agentSetRef: React.MutableRefObject<boolean>;
  sessionAliasesPrintRef: React.MutableRefObject<boolean>;
  sessionAliasesRef: React.MutableRefObject<SessionAliasRow[]>;
}

export function useWsEvents(ctx: WsEventsCtx): void {
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  useEffect(() => {
    const { client } = ctxRef.current;

    const off = client.on((ev: WsEnvelope) => {
      const c = ctxRef.current;
      const markSessionLive = (convId?: string) => {
        if (!convId) return;
        c.setSessionLiveByConv((m) => ({ ...m, [convId]: true }));
      };
      if (ev.type === 'chat_ack') {
        c.setConversationId(ev.data.conv_id);
      } else if (ev.type === 'chat_response') {
        const d = ev.data;
        if (
          d.type === 'status' ||
          d.type === 'stream_event' ||
          d.type === 'result' ||
          d.type === 'context_stats'
        ) {
          markSessionLive((d.conv_id as string | undefined) ?? c.conversationId);
        }
        if (d.type === 'stream_event') {
          const inner = (d as { event?: { type?: string; text?: string; tool?: string; input?: string } }).event;
          if (!inner) return;
          const innerWithResult = inner as { type?: string; tool?: string; result?: string; is_error?: boolean };
          if (innerWithResult.type === 'tool_result' && innerWithResult.tool) {
            // Attach the result preview to the most recent matching
            // call — search blocks bottom-up for the last 'running'
            // tool block with this tool name and update it in place.
            c.setStreaming((s) => {
              if (!s) return s;
              const blocks = (s.blocks ?? []).slice();
              for (let i = blocks.length - 1; i >= 0; i--) {
                const b = blocks[i];
                if (
                  b?.kind === 'tool'
                  && b.call.tool === innerWithResult.tool
                  && b.call.status === 'running'
                ) {
                  blocks[i] = {
                    kind: 'tool',
                    call: {
                      ...b.call,
                      status: innerWithResult.is_error ? 'error' : 'done',
                      result: innerWithResult.result,
                    },
                  };
                  break;
                }
              }
              return { ...s, blocks };
            });
            return;
          }
          if (inner.type === 'text' && typeof inner.text === 'string') {
            const delta = inner.text;
            upsertStreamingText(c.setStreaming, delta);
            c.setActivity((a) => {
              if (!a) return a;
              return {
                ...a,
                verb: 'Streaming',
                streamedChars: (a.streamedChars ?? 0) + delta.length,
                streamStartedAt: a.streamStartedAt ?? Date.now(),
              };
            });
          } else if (inner.type === 'tool_use' && inner.tool) {
            appendStreamingTool(c.setStreaming, inner.tool, inner.input);
            c.setActivity((a) =>
              a
                ? {
                    ...a,
                    verb: `Calling ${inner.tool}`,
                    detail: inner.input ? inner.input.slice(0, 50) : undefined,
                  }
                : a,
            );
          }
        } else if (d.type === 'result' && typeof d.content === 'string') {
          const text = d.content as string;
          finalizeStreamingTools(c.setStreaming);
          // Ring the terminal bell if the turn took long enough that
          // the user might have switched away. 5s threshold matches
          // Claude Code's default. Suppressed via /bell.
          c.setActivity((a) => {
            if (
              c.bellEnabled
              && a
              && Date.now() - a.startedAt > 5000
            ) {
              process.stdout.write('\x07');
            }
            return null;
          });
          c.setStreaming((s) => {
            // Preserve the streamed block sequence so the committed
            // turn renders text + tool calls in the order they
            // actually arrived from the model. Fall back to a single
            // text block if nothing was streamed (no blocks).
            const blocks: TurnBlock[] = s?.blocks && s.blocks.length > 0
              ? s.blocks
              : text
                ? [{ kind: 'text', text }]
                : [];
            const final: Turn = {
              id: s?.id ?? `a-${Date.now()}`,
              role: 'assistant',
              text,
              blocks,
            };
            // Move into committed (Static) and clear streaming.
            c.setCommitted((m) => [...m, final]);
            return null;
          });
        } else if (d.type === 'error' && typeof d.content === 'string') {
          c.setStreaming(null);
          c.setCommitted((m) => [
            ...m,
            { id: `e-${Date.now()}`, role: 'system', text: `error: ${d.content as string}` },
          ]);
          c.finishTurn();
        } else if (d.type === 'status' && typeof d.content === 'string') {
          // Server sends "Thinking..." — fold it into the spinner verb
          // so the committed area stays uncluttered.
          c.setActivity((a) =>
            a ? { ...a, verb: (d.content as string).replace(/\.+$/, '') } : a,
          );
        } else if (d.type === 'context_stats') {
          const cs = d as {
            chat?: { input_tokens?: number; output_tokens?: number };
            context_window?: number | null;
            conv_id?: string;
            model?: string;
          };
          // Server tags every context_stats with the conv_id it
          // belongs to. Stash by id so switching branches flips the
          // displayed numbers without losing the others.
          const cid = cs.conv_id ?? c.conversationId;
          if (cid && cs.chat) {
            c.setTokensByConv((m) => ({
              ...m,
              [cid]: { input: cs.chat!.input_tokens, output: cs.chat!.output_tokens },
            }));
          }
          if (
            cid
            && typeof cs.context_window === 'number'
            && cs.context_window > 0
          ) {
            c.setWindowByConv((m) => ({ ...m, [cid]: cs.context_window as number }));
          }
          // Live model from the actual runtime. Trumps agent-default
          // values seeded by stats/agents_list events: those describe
          // what the agent is configured to use, not what the
          // runtime we're talking to right now actually is.
          if (cs.model && cid === c.conversationId) {
            c.setModel(stripProviderPrefix(cs.model));
          }
        }
      } else if (ev.type === 'stats') {
        c.setStats(ev.data);
        if (ev.data.agent?.model) c.setModel(ev.data.agent.model);
        if (ev.data.agent?.id && !c.agentSetRef.current) {
          c.agentSetRef.current = true;
          c.setAgent(ev.data.agent.id);
        }
      } else if (ev.type === 'models_list') {
        const list = ev.data?.models ?? [];
        c.setModelsList(list);
        if (ev.data?.current) c.setModel(ev.data.current);
      } else if (ev.type === 'browser_result') {
        const data = (ev as { data: { verb: string; result: string } }).data;
        c.pushSystem(`[browser ${data.verb}] ${data.result}`);
      } else if (ev.type === 'channel_accounts') {
        c.setChannelAccounts((ev.data ?? []) as ChannelAccountRow[]);
      } else if (ev.type === 'channel_account_added') {
        const data = (ev as { data: { ok?: boolean; channel?: string; account_id?: string; error?: string } }).data;
        if (data?.ok) {
          c.pushSystem(
            `Account added: ${data.channel}:${data.account_id}.\n` +
            `Next: /attach ${data.channel} ${data.account_id} <peer-id>\n` +
            `to bind a contact to the current session.`,
          );
          client.send({ action: 'list_channel_accounts' });
        } else {
          c.pushSystem(`Failed to add account: ${data?.error ?? 'unknown error'}`);
        }
      } else if (ev.type === 'history_list') {
        // Initial snapshot at WS connect — only in-memory webui
        // sessions. /resume sends list_conversations to refresh
        // with disk-based (channel-bound) sessions too.
        c.setPastConversations(ev.data ?? []);
      } else if (ev.type === 'conversations_list') {
        // Richer list including channel-bound sessions on disk.
        // Each entry may carry `source` ("wechat"/"telegram"/…)
        // and `peer_display` (the WeChat nickname etc.) so /resume
        // can tag the picker rows.
        c.setPastConversations(ev.data ?? []);
      } else if (ev.type === 'qr_login') {
        // Server-driven QR-login state machine. Server pushes:
        //   qr_ready    → render the ASCII QR
        //   scanned     → user scanned, awaiting confirm tap
        //   confirmed   → Tencent acknowledged, creds received
        //   done        → credentials saved on disk; bind picker
        //   expired/error → close picker, surface error
        const data = ev.data ?? {};
        const phase = data.phase;
        if (phase === 'qr_ready') {
          c.setQrAscii(data.ascii ?? '(QR rendering not available — install qrcode)');
          c.setQrStatus(`Waiting for scan… (URL: ${data.url ?? ''})`);
        } else if (phase === 'scanned') {
          c.setQrStatus('Scanned. Tap "confirm" on your phone.');
        } else if (phase === 'confirmed') {
          c.setQrStatus('Confirmed. Saving credentials…');
        } else if (phase === 'done') {
          c.setQrAscii(undefined);
          c.setQrStatus(undefined);
          c.setChosenAccount(data.account_id);
          client.send({ action: 'list_channel_accounts' });
          // Login + bind = one logical step. Lazy-create a TUI
          // conversation if needed (server attach_session backs
          // it with an empty SessionDB row).
          const targetConvId = c.conversationId ?? `local_${randomLocalId()}`;
          if (!c.conversationId) {
            c.setConversationId(targetConvId);
          }
          client.send({
            action: 'attach_session',
            session_id: targetConvId,
            channel: data.channel ?? c.chosenChannel,
            account_id: data.account_id ?? c.chosenAccount,
            peer_kind: 'direct',
            peer_id: '*',
          } as never);
          c.pushSystem(
            `✅ Logged in to ${data.channel ?? '?'}:${data.account_id ?? '?'} ` +
            `and bound this conversation to receive every inbound message.\n` +
            `Switch later via /channel.`,
          );
          c.setPickerKind(null);
          c.setChosenChannel(undefined);
          c.setChosenAccount(undefined);
        } else if (phase === 'expired') {
          c.pushSystem('QR code expired. Try /channel again.');
          c.setQrAscii(undefined);
          c.setQrStatus(undefined);
          c.setPickerKind(null);
        } else if (phase === 'error') {
          c.pushSystem(`QR login failed: ${data.message ?? 'unknown error'}`);
          c.setQrAscii(undefined);
          c.setQrStatus(undefined);
          c.setPickerKind(null);
        }
      } else if (ev.type === 'search_results') {
        const data = ev.data ?? { query: '', results: [], total: 0 };
        const query = data.query ?? '';
        const results = (data.results ?? []) as SearchResultRow[];
        c.setContextSearchQuery(query);
        c.setSearchResults(results);
        if (!data.total) {
          c.pushSystem(`No matches for "${data.query}".`);
        } else {
          c.setPickerKind('context_search_results');
        }
      } else if (ev.type === 'channel_bindings') {
        const data = ev.data ?? [];
        const lines = data.length === 0
          ? ['(no channel bindings)']
          : data.map((b: { agent_id?: string; match?: { channel?: string; account_id?: string; peer?: string } }) =>
              `  ${b.match?.channel ?? '*'}:${b.match?.account_id ?? '*'}:${b.match?.peer ?? '*'} → ${b.agent_id ?? '?'}`,
            );
        c.pushSystem(`Channel bindings:\n${lines.join('\n')}`);
      } else if (ev.type === 'session_aliases') {
        const data = ev.data ?? [];
        // Cache the raw rows so the channel-binding picker can
        // show "you'll overwrite X" hints without re-fetching.
        c.sessionAliasesRef.current = data;
        if (c.sessionAliasesPrintRef.current) {
          c.sessionAliasesPrintRef.current = false;
          const lines = data.length === 0
            ? ['(no session aliases)']
            : data.map((a) => {
                const peerStr = a.peer
                  ? `${a.peer.kind ?? 'direct'}:${a.peer.id ?? '?'}`
                  : '?';
                return (
                  `  ${a.channel ?? '?'}:` +
                  `${a.account_id ?? '?'}:${peerStr} → ` +
                  `${a.agent_id ?? '?'}/${a.session_id ?? '?'}`
                );
              });
          c.pushSystem(`Session aliases:\n${lines.join('\n')}`);
        }
      } else if (ev.type === 'session_alias_changed') {
        const d = ev.data;
        // Server kicks one of these on every successful attach /
        // detach. The replaced field carries "you just overwrote
        // X" awareness — the whole point of the attach() refactor.
        if (d?.action === 'attached' && d.replaced) {
          const r = d.replaced;
          c.pushSystem(
            `⚠️  Replaced previous binding ` +
            `${r.channel ?? '?'}:${r.account_id ?? '?'}:` +
            `${r.peer?.id ?? '?'} → ${r.session_id ?? '?'}. ` +
            `Use /aliases to inspect.`,
          );
        }
        // Refresh the cache so subsequent picker descriptions
        // stay accurate. Server doesn't push the full list on
        // attach.
        client.send({ action: 'list_session_aliases' } as never);
      } else if (ev.type === 'conversation_loaded') {
        const data = ev.data as {
          id?: string;
          title?: string;
          messages?: Array<{ role?: string; content?: string }>;
          provider_info?: { model?: string };
          settings?: {
            tools_enabled?: boolean | null;
            thinking_effort?: ThinkingEffort | null;
            permission_mode?: 'ask' | 'auto' | 'bypass' | null;
          };
        };
        if (data.id) c.setConversationId(data.id);
        if (data.title) c.setConversationTitle(data.title);
        if (data.id) {
          c.setSessionLiveByConv((m) => ({ ...m, [data.id!]: false }));
        }
        if (data.provider_info?.model) c.setModel(stripProviderPrefix(data.provider_info.model));
        if (typeof data.settings?.tools_enabled === 'boolean') {
          c.setToolsOn(data.settings.tools_enabled);
        }
        const effort = data.settings?.thinking_effort;
        if (
          effort === 'off'
          || effort === 'minimal'
          || effort === 'low'
          || effort === 'medium'
          || effort === 'high'
          || effort === 'xhigh'
        ) {
          c.setThinkingEffort(effort);
        }
        const mode = data.settings?.permission_mode;
        if (mode === 'ask' || mode === 'auto' || mode === 'bypass') {
          c.setPermissionMode(mode);
        }
        const turns = (data.messages ?? [])
          .filter((m) => m.role && m.content)
          .map((m, i) => ({
            id: `loaded-${data.id}-${i}`,
            role: (m.role === 'assistant' ? 'assistant' : m.role === 'user' ? 'user' : 'system') as
              | 'assistant'
              | 'user'
              | 'system',
            text: m.content ?? '',
          }));
        c.setCommitted(turns);
        c.setStreaming(null);
      } else if (ev.type === 'model_switched') {
        if (ev.data?.model) c.setModel(ev.data.model);
        c.pushSystem(
          `Switched model → ${ev.data?.provider ?? '?'}:${ev.data?.model ?? '?'}`,
        );
      } else if (ev.type === 'agents_list') {
        const list = ev.data as AgentInfo[];
        c.setAgentsList(list);
        const def = list.find((a) => a.default) ?? list[0];
        if (def && !c.agentSetRef.current) {
          c.agentSetRef.current = true;
          c.setAgent(def.id);
          const m = renderModel(def.model);
          if (m) c.setModel(m);
          const effort = def.thinking_effort;
          if (
            effort === 'off'
            || effort === 'minimal'
            || effort === 'low'
            || effort === 'medium'
            || effort === 'high'
            || effort === 'xhigh'
          ) {
            c.setThinkingEffort(effort);
          }
        }
      } else if (ev.type === 'event') {
        const e = ev as { type: 'event'; event: string; data: Record<string, unknown> };
        if (e.event === 'agents') {
          client.send({ action: 'list_agents' });
          client.send({ action: 'stats' });
        }
      } else if (ev.type === 'channel_turn') {
        // Live wechat / telegram inbound. Channels worker just
        // persisted a user message + assistant reply for some
        // session; if the TUI is currently viewing that session,
        // append both turns to the transcript so the chat updates
        // without a /resume refresh.
        const d = ev.data;
        if (d.conv_id !== c.conversationId) return;
        const newTurns: Turn[] = [];
        if (d.user?.text) {
          const tag = d.user.peer_display ? `[${d.user.source ?? 'channel'}:${d.user.peer_display}] ` : '';
          newTurns.push({
            id: d.user.id ?? `cu-${Date.now()}`,
            role: 'user',
            text: tag + d.user.text,
          });
        }
        if (d.assistant?.text) {
          newTurns.push({
            id: d.assistant.id ?? `ca-${Date.now()}`,
            role: 'assistant',
            text: d.assistant.text,
          });
        }
        if (newTurns.length > 0) c.setCommitted((m) => [...m, ...newTurns]);
        if (newTurns.length > 0) markSessionLive(d.conv_id);
      } else if (ev.type === 'error') {
        const data = (ev as { data?: { message?: string } }).data;
        const msg = data?.message ?? 'unknown error';
        c.setCommitted((m) => [...m, { id: `e-${Date.now()}`, role: 'system', text: `error: ${msg}` }]);
        c.finishTurn();
      }
    });
    const offState = client.onState((s) => ctxRef.current.setConnState(s));
    client.send({ action: 'stats' });
    client.send({ action: 'list_agents' });
    // Boot-time prefetch of alias rows into sessionAliasesRef.
    // Silent (sessionAliasesPrintRef stays false) — purely so the
    // channel picker can render "you'll overwrite X" hints
    // without latency when /channel is opened later.
    client.send({ action: 'list_session_aliases' });
    trimHistoryFile();
    return () => {
      off();
      offState();
    };
  }, [ctx.client]);
}

// Re-export PendingAttach so REPL.tsx can import everything from
// useWsEvents without also importing types.ts a second time.
export type { PendingAttach };
