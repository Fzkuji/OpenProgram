import React, { useEffect, useState, useRef } from 'react';
import { writeFileSync } from 'fs';
import { join } from 'path';
import { Box, Text, useApp, useInput } from '@openprogram/ink';
import { BackendClient, WsEnvelope, StatsEnvelope, ConnectionState } from '../ws/client.js';
import { BottomBar } from '../components/BottomBar.js';
import { Messages } from '../components/Messages.js';
import { Spinner } from '../components/Spinner.js';
import { Picker, PickerItem } from '../components/Picker.js';
import { LineInput } from '../components/LineInput.js';
import { Turn, ToolCall, TurnBlock } from '../components/Turn.js';
import { PromptInput } from '../components/PromptInput/PromptInput.js';
import { handleSlash } from '../commands/handler.js';
import { loadHistory, appendHistory, trimHistoryFile } from '../utils/history.js';
import { copyToClipboard } from '../utils/clipboard.js';
import { useTheme } from '../theme/ThemeProvider.js';
import { isThemeSetting } from '../theme/themes.js';
import { ThemePicker } from '../components/ThemePicker.js';

export interface REPLProps {
  client: BackendClient;
  initialAgent?: string;
  initialConversation?: string;
}

interface AgentInfo {
  id: string;
  name: string;
  model?: string | { provider?: string; id?: string };
  default?: boolean;
}

const tsToDate = (ts?: number): string => {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return d.toLocaleDateString();
};

const renderModel = (m: AgentInfo['model']): string | undefined => {
  if (!m) return undefined;
  if (typeof m === 'string') return m;
  return m.id ?? m.provider;
};

/** Some runtimes embed the provider as a prefix in ``runtime.model`` (e.g.
 *  Codex emits ``openai-codex:gpt-5.4``). Strip it for display so the
 *  BottomBar only shows the bare model id. */
const stripProviderPrefix = (m: string | undefined): string | undefined => {
  if (!m) return m;
  const idx = m.indexOf(':');
  if (idx <= 0) return m;
  return m.slice(idx + 1);
};

interface Activity {
  /** Verb shown next to the spinner — "Thinking", "Calling Bash", etc. */
  verb: string;
  /** Optional inline detail — usually the truncated tool input. */
  detail?: string;
  /** Wall clock when this turn started (used for elapsed display). */
  startedAt: number;
  /** Cumulative characters received from text deltas. */
  streamedChars?: number;
  /** Wall clock when streaming first started (text first delta). */
  streamStartedAt?: number;
}

export const REPL: React.FC<REPLProps> = ({ client, initialAgent, initialConversation }) => {
  const app = useApp();
  const [committed, setCommitted] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState<Turn | null>(null);
  const [agent, setAgent] = useState<string | undefined>(initialAgent);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversation);
  const [activity, setActivity] = useState<Activity | null>(null);
  const [stats, setStats] = useState<StatsEnvelope['data'] | undefined>(undefined);
  const [tick, setTick] = useState(0);
  const [slashMode, setSlashMode] = useState(false);
  // Per-conversation token + context-window tracking. We key by conv_id
  // so switching branches (resume / new / load_conversation) flips the
  // BottomBar indicator to that branch's own usage.
  const [tokensByConv, setTokensByConv] = useState<
    Record<string, { input?: number; output?: number }>
  >({});
  const [windowByConv, setWindowByConv] = useState<Record<string, number>>({});
  const [history, setHistory] = useState<string[]>(() => loadHistory());
  const [conversationTitle, setConversationTitle] = useState<string | undefined>(undefined);
  const [bellEnabled, setBellEnabled] = useState(true);
  const [modelsList, setModelsList] = useState<string[]>([]);
  const [pastConversations, setPastConversations] = useState<
    Array<{
      id?: string;
      title?: string;
      created_at?: number;
      /** Channel name for channel-bound sessions ("wechat", "telegram", …). */
      source?: string;
      /** Display name for the bound peer (e.g. WeChat nickname). */
      peer_display?: string;
    }>
  >([]);
  const [pickerKind, setPickerKind] = useState<
    null
    | 'model' | 'resume' | 'agent' | 'channel' | 'channel_account' | 'theme'
    | 'register_account_id' | 'register_token'
    // Three new picker states for in-TUI channel binding:
    //   - channel_action: after picking channel+account, choose
    //     binding mode (catch-all this conv / per-peer / list).
    //   - channel_peer_input: prompt for peer_id (e.g. wxid_xxx).
    //   - channel_qr_wait: show ASCII QR + status while wechat
    //     login is in progress.
    | 'channel_action' | 'channel_peer_input' | 'channel_qr_wait'
  >(null);
  const [registerForm, setRegisterForm] = useState<{
    channel?: string;
    accountId?: string;
  }>({});
  const { setThemeSetting, currentTheme } = useTheme();
  const [channelAccounts, setChannelAccounts] = useState<
    Array<{ channel?: string; account_id?: string; configured?: boolean }>
  >([]);
  const [chosenChannel, setChosenChannel] = useState<string | undefined>(undefined);
  // Channel-binding scratch state — held while the user walks
  // through the channel→account→action→peer picker chain.
  const [chosenAccount, setChosenAccount] = useState<string | undefined>(undefined);
  // QR login progress: the ASCII art for the current QR + a status
  // message ("scanned", "waiting", etc.). Cleared when the picker
  // closes.
  const [qrAscii, setQrAscii] = useState<string | undefined>(undefined);
  const [qrStatus, setQrStatus] = useState<string | undefined>(undefined);
  const [agentsList, setAgentsList] = useState<AgentInfo[]>([]);
  const [toolsOn, setToolsOn] = useState(true);
  // Permission cycle: ask before each tool call → auto-approve safe → bypass everything.
  const [permissionMode, setPermissionMode] = useState<'ask' | 'auto' | 'bypass'>('ask');
  // Thinking effort cycle: off → low → medium → high → off.
  const [thinkingEffort, setThinkingEffort] = useState<'off' | 'low' | 'medium' | 'high'>('medium');
  const [connState, setConnState] = useState<ConnectionState>(client.getState());
  const agentSetRef = useRef(false);
  // Theme switch: with hermes-ink every render is a full cell-grid
  // frame, so changing useColors() context just re-renders the entire
  // tree with the new palette — no Static remount or nonce needed.
  const lastThemeRef = useRef<string>(currentTheme);
  useEffect(() => {
    if (lastThemeRef.current !== currentTheme) {
      lastThemeRef.current = currentTheme;
    }
  }, [currentTheme]);

  // 1Hz tick for elapsed-seconds display while a turn is active.
  useEffect(() => {
    if (!activity) return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [activity]);



  const pushSystem = (text: string) =>
    setCommitted((m) => [
      ...m,
      { id: `s-${Date.now()}-${m.length}`, role: 'system', text },
    ]);

  const startTurn = (verb: string) =>
    setActivity({ verb, startedAt: Date.now() });

  const finishTurn = () => setActivity(null);

  // Streaming-turn state is built as an ordered list of blocks (text or
  // tool) so the rendered transcript reflects the actual emit order:
  // a tool call shows where the model called it, not at the bottom of
  // the turn. The flat `text` field is also kept (concatenation of all
  // text segments) so anything that just wants the body — /copy,
  // exportTranscript — keeps working unchanged.
  const newAssistantTurn = (): Turn => ({
    id: `a-${Date.now()}`,
    role: 'assistant',
    text: '',
    blocks: [],
    streaming: true,
  });

  const upsertStreamingText = (delta: string) => {
    setStreaming((s) => {
      const base: Turn = s ?? newAssistantTurn();
      const blocks = (base.blocks ?? []).slice();
      const last = blocks[blocks.length - 1];
      if (last && last.kind === 'text') {
        blocks[blocks.length - 1] = { kind: 'text', text: last.text + delta };
      } else {
        blocks.push({ kind: 'text', text: delta });
      }
      return {
        ...base,
        text: (base.text ?? '') + delta,
        blocks,
        streaming: true,
      };
    });
  };

  const appendStreamingTool = (tool: string, input?: string) => {
    setStreaming((s) => {
      const base: Turn = s ?? newAssistantTurn();
      const blocks = (base.blocks ?? []).slice();
      const callId = `t-${Date.now()}-${blocks.length}`;
      const call: ToolCall = { id: callId, tool, input, status: 'running' };
      blocks.push({ kind: 'tool', call });
      return { ...base, blocks, streaming: true };
    });
  };

  const finalizeStreamingTools = () => {
    setStreaming((s) => {
      if (!s) return s;
      const blocks = (s.blocks ?? []).map((b): TurnBlock =>
        b.kind === 'tool' && b.call.status === 'running'
          ? { kind: 'tool', call: { ...b.call, status: 'done' as const } }
          : b,
      );
      return { ...s, blocks };
    });
  };

  useEffect(() => {
    const off = client.on((ev: WsEnvelope) => {
      if (ev.type === 'chat_ack') {
        setConversationId(ev.data.conv_id);
      } else if (ev.type === 'chat_response') {
        const d = ev.data;
        if (d.type === 'stream_event') {
          const inner = (d as { event?: { type?: string; text?: string; tool?: string; input?: string } }).event;
          if (!inner) return;
          const innerWithResult = inner as { type?: string; tool?: string; result?: string; is_error?: boolean };
          if (innerWithResult.type === 'tool_result' && innerWithResult.tool) {
            // Attach the result preview to the most recent matching call
            // — search blocks bottom-up for the last 'running' tool block
            // with this tool name and update it in place.
            setStreaming((s) => {
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
            upsertStreamingText(delta);
            setActivity((a) => {
              if (!a) return a;
              return {
                ...a,
                verb: 'Streaming',
                streamedChars: (a.streamedChars ?? 0) + delta.length,
                streamStartedAt: a.streamStartedAt ?? Date.now(),
              };
            });
          } else if (inner.type === 'tool_use' && inner.tool) {
            appendStreamingTool(inner.tool, inner.input);
            setActivity((a) =>
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
          finalizeStreamingTools();
          // Ring the terminal bell if the turn took long enough that the
          // user might have switched away. 5s threshold matches Claude
          // Code's default. Suppressed via /bell.
          setActivity((a) => {
            if (
              bellEnabled
              && a
              && Date.now() - a.startedAt > 5000
            ) {
              process.stdout.write('\x07');
            }
            return null;
          });
          setStreaming((s) => {
            // Preserve the streamed block sequence so the committed
            // turn renders text + tool calls in the order they actually
            // arrived from the model. If somehow nothing was streamed
            // (no blocks), fall back to a single text block built from
            // the final result content.
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
            setCommitted((m) => [...m, final]);
            return null;
          });
        } else if (d.type === 'error' && typeof d.content === 'string') {
          setStreaming(null);
          setCommitted((m) => [
            ...m,
            { id: `e-${Date.now()}`, role: 'system', text: `error: ${d.content as string}` },
          ]);
          finishTurn();
        } else if (d.type === 'status' && typeof d.content === 'string') {
          // Server sends "Thinking..." — fold it into the spinner verb so the
          // committed area stays uncluttered.
          setActivity((a) =>
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
          const cid = cs.conv_id ?? conversationId;
          if (cid && cs.chat) {
            setTokensByConv((m) => ({
              ...m,
              [cid]: { input: cs.chat!.input_tokens, output: cs.chat!.output_tokens },
            }));
          }
          if (
            cid
            && typeof cs.context_window === 'number'
            && cs.context_window > 0
          ) {
            setWindowByConv((m) => ({ ...m, [cid]: cs.context_window as number }));
          }
          // Live model from the actual runtime. Trumps agent-default
          // values seeded by stats/agents_list events: those describe
          // what the agent is configured to use, not what the runtime
          // we're talking to right now actually is.
          if (cs.model && cid === conversationId) {
            setModel(stripProviderPrefix(cs.model));
          }
        }
      } else if (ev.type === 'stats') {
        setStats(ev.data);
        if (ev.data.agent?.model) setModel(ev.data.agent.model);
        if (ev.data.agent?.id && !agentSetRef.current) {
          agentSetRef.current = true;
          setAgent(ev.data.agent.id);
        }
      } else if (ev.type === 'models_list') {
        const list = ev.data?.models ?? [];
        setModelsList(list);
        if (ev.data?.current) setModel(ev.data.current);
      } else if (ev.type === 'browser_result') {
        const data = (ev as { data: { verb: string; result: string } }).data;
        pushSystem(`[browser ${data.verb}] ${data.result}`);
      } else if (ev.type === 'channel_accounts') {
        setChannelAccounts((ev.data ?? []) as Array<{ channel?: string; account_id?: string; configured?: boolean }>);
      } else if (ev.type === 'channel_account_added') {
        const data = (ev as { data: { ok?: boolean; channel?: string; account_id?: string; error?: string } }).data;
        if (data?.ok) {
          pushSystem(
            `Account added: ${data.channel}:${data.account_id}.\n` +
            `Next: /attach ${data.channel} ${data.account_id} <peer-id>\n` +
            `to bind a contact to the current session.`,
          );
          client.send({ action: 'list_channel_accounts' });
        } else {
          pushSystem(`Failed to add account: ${data?.error ?? 'unknown error'}`);
        }
      } else if (ev.type === 'history_list') {
        // Initial snapshot at WS connect — only in-memory webui sessions.
        // /resume sends list_conversations to refresh with disk-based
        // (channel-bound) sessions too.
        setPastConversations(ev.data ?? []);
      } else if (ev.type === 'conversations_list') {
        // Richer list including channel-bound sessions on disk. Each
        // entry may carry `source` ("wechat"/"telegram"/…) and
        // `peer_display` (the WeChat nickname etc.) so /resume can tag
        // the picker rows.
        setPastConversations(ev.data ?? []);
      } else if (ev.type === 'qr_login') {
        // Server-driven QR-login state machine. Server pushes:
        //   qr_ready    → render the ASCII QR
        //   scanned     → user scanned, awaiting confirm tap
        //   confirmed   → Tencent acknowledged, creds received
        //   done        → credentials saved on disk; jump to bind-picker
        //   expired/error → close picker, surface error
        const data = ev.data ?? {};
        const phase = data.phase;
        if (phase === 'qr_ready') {
          setQrAscii(data.ascii ?? '(QR rendering not available — install qrcode)');
          setQrStatus(`Waiting for scan… (URL: ${data.url ?? ''})`);
        } else if (phase === 'scanned') {
          setQrStatus('Scanned. Tap "confirm" on your phone.');
        } else if (phase === 'confirmed') {
          setQrStatus('Confirmed. Saving credentials…');
        } else if (phase === 'done') {
          pushSystem(`✅ Logged in to ${data.channel ?? '?'}:${data.account_id ?? '?'}.`);
          setQrAscii(undefined);
          setQrStatus(undefined);
          setChosenAccount(data.account_id);
          // Refresh account list, then drop into the binding picker
          // so the user can immediately attach the new account to the
          // current conversation.
          client.send({ action: 'list_channel_accounts' });
          setPickerKind('channel_action');
        } else if (phase === 'expired') {
          pushSystem('QR code expired. Try /channel again.');
          setQrAscii(undefined);
          setQrStatus(undefined);
          setPickerKind(null);
        } else if (phase === 'error') {
          pushSystem(`QR login failed: ${data.message ?? 'unknown error'}`);
          setQrAscii(undefined);
          setQrStatus(undefined);
          setPickerKind(null);
        }
      } else if (ev.type === 'search_results') {
        // SessionDB FTS5 hits — render each as a system note so the
        // user can pick a session_id to /resume from. Picker
        // integration (open with these as items) is a follow-up;
        // for now the inline list is enough to find what you typed
        // /search for.
        const data = ev.data ?? { query: '', results: [], total: 0 };
        if (!data.total) {
          pushSystem(`No matches for "${data.query}".`);
        } else {
          const lines = [`Search "${data.query}" — ${data.total} result(s):`];
          for (const r of data.results.slice(0, 20)) {
            const titleStr = r.session_title ?? r.session_id ?? '?';
            const sourceTag = r.session_source ? ` [${r.session_source}]` : '';
            const roleTag = r.role === 'user' ? '👤' : '🤖';
            lines.push(`  ${roleTag} ${titleStr}${sourceTag}`);
            lines.push(`     ${r.preview}`);
            lines.push(`     /resume ${r.session_id}`);
          }
          if (data.total > 20) {
            lines.push(`  … and ${data.total - 20} more (refine your query)`);
          }
          pushSystem(lines.join('\n'));
        }
      } else if (ev.type === 'channel_bindings') {
        const data = ev.data ?? [];
        const lines = data.length === 0
          ? ['(no channel bindings)']
          : data.map((b: { agent_id?: string; match?: { channel?: string; account_id?: string; peer?: string } }) =>
              `  ${b.match?.channel ?? '*'}:${b.match?.account_id ?? '*'}:${b.match?.peer ?? '*'} → ${b.agent_id ?? '?'}`,
            );
        pushSystem(`Channel bindings:\n${lines.join('\n')}`);
      } else if (ev.type === 'session_aliases') {
        const data = ev.data ?? [];
        const lines = data.length === 0
          ? ['(no session aliases)']
          : data.map((a: { channel?: string; account_id?: string; peer?: string; agent_id?: string; conversation_id?: string }) =>
              `  ${a.channel ?? '?'}:${a.account_id ?? '?'}:${a.peer ?? '?'} → ${a.agent_id ?? '?'}/${a.conversation_id ?? '?'}`,
            );
        pushSystem(`Session aliases:\n${lines.join('\n')}`);
      } else if (ev.type === 'conversation_loaded') {
        const data = ev.data as {
          id?: string;
          title?: string;
          messages?: Array<{ role?: string; content?: string }>;
          provider_info?: { model?: string };
        };
        if (data.id) setConversationId(data.id);
        if (data.title) setConversationTitle(data.title);
        if (data.provider_info?.model) setModel(stripProviderPrefix(data.provider_info.model));
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
        setCommitted(turns);
        setStreaming(null);
      } else if (ev.type === 'model_switched') {
        if (ev.data?.model) setModel(ev.data.model);
        pushSystem(
          `Switched model → ${ev.data?.provider ?? '?'}:${ev.data?.model ?? '?'}`,
        );
      } else if (ev.type === 'agents_list') {
        const list = ev.data as AgentInfo[];
        setAgentsList(list);
        const def = list.find((a) => a.default) ?? list[0];
        if (def && !agentSetRef.current) {
          agentSetRef.current = true;
          setAgent(def.id);
          const m = renderModel(def.model);
          if (m) setModel(m);
        }
      } else if (ev.type === 'event') {
        const e = ev as { type: 'event'; event: string; data: Record<string, unknown> };
        if (e.event === 'agents') {
          client.send({ action: 'list_agents' });
          client.send({ action: 'stats' });
        }
      } else if (ev.type === 'channel_turn') {
        // Live wechat / telegram inbound. Channels worker just persisted
        // a user message + assistant reply for some session; if the TUI
        // is currently viewing that session, append both turns to the
        // transcript so the chat updates without a /resume refresh.
        const d = ev.data;
        if (d.conv_id !== conversationId) return;
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
        if (newTurns.length > 0) setCommitted((m) => [...m, ...newTurns]);
      } else if (ev.type === 'error') {
        const data = (ev as { data?: { message?: string } }).data;
        const msg = data?.message ?? 'unknown error';
        setCommitted((m) => [...m, { id: `e-${Date.now()}`, role: 'system', text: `error: ${msg}` }]);
        finishTurn();
      }
    });
    const offState = client.onState((s) => setConnState(s));
    client.send({ action: 'stats' });
    client.send({ action: 'list_agents' });
    trimHistoryFile();
    return () => {
      off();
      offState();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  // Double-press Ctrl+C to exit (Claude Code / Hermes pattern).
  // First press: surface a "Press Ctrl+C again to exit" hint in
  // BottomBar and start an 800 ms timer. Second press inside the
  // window: app.exit(). Timer expires: clear the hint and reset.
  const [exitPending, setExitPending] = useState(false);
  const exitTimerRef = useRef<NodeJS.Timeout | null>(null);
  const lastCtrlCRef = useRef<number>(0);

  useEffect(() => () => {
    if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
  }, []);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      const now = Date.now();
      const recent = now - lastCtrlCRef.current <= 800
        && exitTimerRef.current !== null;
      if (recent) {
        if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
        exitTimerRef.current = null;
        setExitPending(false);
        app.exit();
        return;
      }
      lastCtrlCRef.current = now;
      setExitPending(true);
      if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
      exitTimerRef.current = setTimeout(() => {
        exitTimerRef.current = null;
        setExitPending(false);
      }, 800);
      return;
    }
    // shift+tab cycles permission mode (Claude Code parity):
    // ask → auto → bypass → ask. Always ask, auto-approve safe, or bypass all.
    if (key.shift && key.tab) {
      setPermissionMode((m) => (m === 'ask' ? 'auto' : m === 'auto' ? 'bypass' : 'ask'));
      return;
    }
    // tab cycles thinking effort: off → low → medium → high → off.
    if (key.tab && !key.shift) {
      setThinkingEffort((t) =>
        t === 'off' ? 'low' : t === 'low' ? 'medium' : t === 'medium' ? 'high' : 'off',
      );
      return;
    }
    // Esc closes the channel_qr_wait picker (no input form to absorb
    // it). Other pickers handle their own onCancel via Picker/LineInput
    // — this is just for the read-only QR display.
    if (key.escape && pickerKind === 'channel_qr_wait') {
      pushSystem('QR login cancelled.');
      setQrAscii(undefined);
      setQrStatus(undefined);
      setPickerKind(null);
      return;
    }
  });

  const onSubmit = (text: string) => {
    if (!text.trim()) return;
    // Save EVERY submitted line — chat messages and slash commands —
    // to up-arrow history. Previously only non-slash-handled inputs
    // landed in history; slash commands like `/channel` would
    // disappear after submit and ↑ wouldn't bring them back.
    setHistory((h) => {
      if (h[h.length - 1] === text) return h;
      appendHistory(text);
      return [...h, text].slice(-500);
    });
    if (text.startsWith('/')) {
      const handled = handleSlash(text, {
        client,
        pushSystem,
        clearCommitted: () => setCommitted([]),
        newSession: () => {
          setConversationId(undefined);
          setStreaming(null);
          setCommitted([]);
        },
        exit: () => app.exit(),
        openPicker: (kind) => setPickerKind(kind),
        toggleTools: () => setToolsOn((on) => !on),
        toggleBell: () => {
          let next = bellEnabled;
          setBellEnabled((b) => {
            next = !b;
            return next;
          });
          return next;
        },
        showWelcome: () => {
          if (!stats) {
            pushSystem('Stats not loaded yet — try again in a moment.');
            return;
          }
          const lines = [
            `OpenProgram · ${stats.agent?.name ?? '—'} · ${stats.agent?.model ?? '—'}`,
            `${stats.programs_count ?? 0} programs · ${stats.skills_count ?? 0} skills · ${stats.agents_count ?? 0} agents · ${stats.conversations_count ?? 0} sessions`,
          ];
          if (stats.top_programs?.length) {
            lines.push(`programs: ${stats.top_programs.map((p) => p.name).filter(Boolean).join(' · ')}`);
          }
          if (stats.top_skills?.length) {
            lines.push(`skills: ${stats.top_skills.map((s) => s.name).filter(Boolean).join(' · ')}`);
          }
          pushSystem(lines.join('\n'));
        },
        showAgentInfo: () => {
          const a = agentsList.find((x) => x.id === agent);
          if (!a) {
            pushSystem('No active agent.');
            return;
          }
          const lines = [
            `agent: ${a.name ?? a.id}  (${a.id})`,
            `model: ${renderModel(a.model) ?? '—'}`,
            `default: ${a.default ? 'yes' : 'no'}`,
          ];
          pushSystem(lines.join('\n'));
        },
        lastAssistantText: () => {
          for (let i = committed.length - 1; i >= 0; i--) {
            if (committed[i]?.role === 'assistant') return committed[i]!.text;
          }
          return null;
        },
        copyToClipboard: copyToClipboard,
        exportTranscript: (filename) => {
          const fname = filename ?? `openprogram-${Date.now()}.md`;
          const path = fname.startsWith('/') ? fname : join(process.cwd(), fname);
          const lines: string[] = [
            `# OpenProgram session ${conversationId ?? '(unsaved)'}`,
            `agent: ${agent ?? '—'}`,
            `model: ${model ?? '—'}`,
            '',
          ];
          for (const t of committed) {
            lines.push(`## ${t.role}`);
            lines.push('');
            lines.push(t.text);
            lines.push('');
            for (const tc of t.tools ?? []) {
              lines.push(`- tool: \`${tc.tool}\` ${tc.input ? `· ${tc.input}` : ''}`);
            }
            if ((t.tools ?? []).length) lines.push('');
          }
          writeFileSync(path, lines.join('\n'));
          return path;
        },
        currentAgent: agent,
        currentModel: model,
        currentConversation: conversationId,
        setTheme: (name: string) => {
          if (!isThemeSetting(name)) return false;
          setThemeSetting(name);
          return true;
        },
      });
      if (handled) return;
    }
    setCommitted((m) => [...m, { id: `u-${Date.now()}`, role: 'user', text }]);
    if (!conversationTitle && committed.length === 0) {
      // Mirror server-side behaviour: first user message becomes the title.
      setConversationTitle(text.slice(0, 50) + (text.length > 50 ? '…' : ''));
    }
    startTurn('Thinking');
    client.send({
      action: 'chat',
      conv_id: conversationId,
      agent_id: agent,
      text,
      tools: toolsOn,
      thinking_effort: thinkingEffort === 'off' ? undefined : thinkingEffort,
    } as never);
  };

  const onCancel = () => {
    if (!conversationId) return;
    client.send({ action: 'stop', conv_id: conversationId });
    setStreaming(null);
    finishTurn();
    pushSystem('Stopped.');
  };

  const elapsed = activity ? (Date.now() - activity.startedAt) / 1000 : undefined;
  void tick; // depend on tick so elapsed re-renders every second
  const streamRate = (() => {
    if (!activity?.streamStartedAt || !activity.streamedChars) return undefined;
    const dt = (Date.now() - activity.streamStartedAt) / 1000;
    if (dt <= 0.1) return undefined;
    return Math.round(activity.streamedChars / dt);
  })();

  // Build picker items based on the current pickerKind.
  let pickerNode: React.ReactElement | null = null;
  if (pickerKind === 'model') {
    const items: PickerItem<string>[] = modelsList.map((m) => ({
      label: m,
      description: m === model ? 'current' : undefined,
      value: m,
    }));
    pickerNode = (
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
  } else if (pickerKind === 'agent') {
    const items: PickerItem<string>[] = agentsList.map((a) => ({
      label: a.name || a.id,
      description: a.default
        ? `${a.id} · default`
        : a.id,
      value: a.id,
    }));
    pickerNode = (
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
  } else if (pickerKind === 'channel') {
    const channels = ['wechat', 'telegram', 'discord', 'slack'];
    const items: PickerItem<string>[] = channels.map((ch) => ({
      label: ch,
      description:
        channelAccounts.filter((a) => a.channel === ch).length > 0
          ? `${channelAccounts.filter((a) => a.channel === ch).length} account(s) configured`
          : 'no account yet',
      value: ch,
    }));
    pickerNode = (
      <Picker
        title="Choose a channel"
        items={items}
        onSelect={(it) => {
          setChosenChannel(it.value);
          setPickerKind('channel_account');
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  } else if (pickerKind === 'channel_account') {
    const filtered = channelAccounts.filter((a) => a.channel === chosenChannel);
    const accountItems: PickerItem<string>[] = filtered.map((a) => ({
      label: a.account_id ?? '',
      description: a.configured ? 'logged in' : 'not configured',
      value: a.account_id ?? '',
    }));
    const isTokenChannel =
      chosenChannel === 'discord' || chosenChannel === 'telegram' || chosenChannel === 'slack';
    const items: PickerItem<string>[] = [
      ...accountItems,
      isTokenChannel
        ? { label: '+ Register new', description: 'paste a bot token to add an account', value: '__register__' }
        : { label: '+ Register new', description: 'wechat needs shell QR — select for command', value: '__register_wechat__' },
    ];
    pickerNode = (
      <Picker
        title={`Pick a ${chosenChannel} account`}
        items={items}
        onSelect={(it) => {
          if (it.value === '__register__') {
            setRegisterForm({ channel: chosenChannel });
            setPickerKind('register_account_id');
            return;
          }
          if (it.value === '__register_wechat__') {
            // In-TUI QR login. Server pushes qr_login envelopes; the
            // channel_qr_wait picker renders them and switches to
            // channel_action when login finishes.
            const acctId = `default_${Date.now().toString(36).slice(-4)}`;
            setChosenAccount(acctId);
            setQrAscii(undefined);
            setQrStatus('Requesting QR code…');
            client.send({
              action: 'start_channel_login',
              channel: 'wechat',
              account_id: 'default',
            } as never);
            setPickerKind('channel_qr_wait');
            return;
          }
          // Existing-account path: continue to binding-mode picker.
          setChosenAccount(it.value);
          setPickerKind('channel_action');
        }}
        onCancel={() => {
          setPickerKind('channel');
        }}
      />
    );
  } else if (pickerKind === 'channel_action') {
    // Three-way: catch-all to current conversation, specific peer,
    // or just list/delete existing bindings. Catch-all means every
    // inbound message on this channel:account lands in conversationId
    // — useful for "I want all my wechat replies in this TUI session".
    const items: PickerItem<string>[] = [
      {
        label: 'Bind ALL inbound to this conversation',
        description: conversationId
          ? `Every ${chosenChannel}:${chosenAccount} message → current chat`
          : 'Send a message first to create a session',
        value: '__catchall__',
      },
      {
        label: 'Bind a specific peer to this conversation',
        description: 'You will be prompted for the peer id (wxid_xxx etc.)',
        value: '__peer__',
      },
      {
        label: 'Show existing bindings',
        description: 'List + remove rules later',
        value: '__list__',
      },
    ];
    pickerNode = (
      <Picker
        title={`Bind ${chosenChannel}:${chosenAccount} how?`}
        items={items}
        onSelect={(it) => {
          if (it.value === '__list__') {
            client.send({ action: 'list_session_aliases' } as never);
            client.send({ action: 'list_channel_bindings' } as never);
            setPickerKind(null);
            setChosenChannel(undefined);
            setChosenAccount(undefined);
            return;
          }
          if (it.value === '__catchall__') {
            if (!conversationId) {
              pushSystem('Send a message first so there is a conversation to bind.');
              return;
            }
            // Catch-all = attach with peer_id="*". The bindings/route
            // logic falls through to alias.lookup which matches any
            // peer for this (channel, account) when peer_id == "*".
            client.send({
              action: 'attach_session',
              session_id: conversationId,
              channel: chosenChannel,
              account_id: chosenAccount,
              peer_kind: 'direct',
              peer_id: '*',
            } as never);
            pushSystem(
              `✅ Bound ${chosenChannel}:${chosenAccount} (catch-all) → current conversation. ` +
              `Channel worker will route every inbound message here.`,
            );
            setPickerKind(null);
            setChosenChannel(undefined);
            setChosenAccount(undefined);
            return;
          }
          if (it.value === '__peer__') {
            if (!conversationId) {
              pushSystem('Send a message first so there is a conversation to bind.');
              return;
            }
            setPickerKind('channel_peer_input');
            return;
          }
        }}
        onCancel={() => setPickerKind('channel_account')}
      />
    );
  } else if (pickerKind === 'channel_peer_input') {
    pickerNode = (
      <LineInput
        label={`Peer ID for ${chosenChannel}:${chosenAccount}`}
        hint="e.g. wxid_xxxx for WeChat. The bot's worker log shows them once messages arrive."
        onSubmit={(v) => {
          const peerId = v.trim();
          if (!peerId) {
            pushSystem('peer id required.');
            return;
          }
          if (!conversationId) {
            pushSystem('No active conversation.');
            return;
          }
          client.send({
            action: 'attach_session',
            session_id: conversationId,
            channel: chosenChannel,
            account_id: chosenAccount,
            peer_kind: 'direct',
            peer_id: peerId,
          } as never);
          pushSystem(`✅ Bound ${chosenChannel}:${chosenAccount}:${peerId} → current conversation.`);
          setPickerKind(null);
          setChosenChannel(undefined);
          setChosenAccount(undefined);
        }}
        onCancel={() => setPickerKind('channel_action')}
      />
    );
  } else if (pickerKind === 'channel_qr_wait') {
    // Read-only "picker" — no input, just renders the QR + status
    // until the qr_login envelope handler advances us out.
    pickerNode = (
      <Box flexDirection="column" borderStyle="single" paddingX={1} paddingY={0}>
        <Text bold>Scan to log in to {chosenChannel}</Text>
        <Text color="ansi:blackBright">Open WeChat on your phone → tap [+] → "Scan QR"</Text>
        {qrAscii ? <Text>{qrAscii}</Text> : <Text color="ansi:blackBright">Loading QR…</Text>}
        <Text color="ansi:cyan">{qrStatus ?? ''}</Text>
        <Text color="ansi:blackBright">(esc to cancel)</Text>
      </Box>
    );
  } else if (pickerKind === 'register_account_id') {
    pickerNode = (
      <LineInput
        label={`Register ${registerForm.channel ?? '?'} account`}
        hint="Choose a short id you'll use to refer to this account (e.g. 'default', 'work')."
        initial="default"
        onSubmit={(v) => {
          const id = v.trim();
          if (!id) {
            pushSystem('account_id required.');
            return;
          }
          setRegisterForm((f) => ({ ...f, accountId: id }));
          setPickerKind('register_token');
        }}
        onCancel={() => {
          setPickerKind('channel_account');
          setRegisterForm({});
        }}
      />
    );
  } else if (pickerKind === 'register_token') {
    pickerNode = (
      <LineInput
        label={`${registerForm.channel ?? '?'} bot token for "${registerForm.accountId}"`}
        hint="Paste the bot token from your provider dashboard."
        mask
        onSubmit={(token) => {
          const t = token.trim();
          if (!t) {
            pushSystem('token required.');
            return;
          }
          if (!registerForm.channel || !registerForm.accountId) {
            pushSystem('register form incomplete; aborting.');
            setPickerKind(null);
            setRegisterForm({});
            return;
          }
          client.send({
            action: 'add_channel_account',
            channel: registerForm.channel,
            account_id: registerForm.accountId,
            token: t,
          });
          setPickerKind(null);
          setRegisterForm({});
          setChosenChannel(undefined);
          client.send({ action: 'list_channel_accounts' });
        }}
        onCancel={() => {
          setPickerKind('register_account_id');
        }}
      />
    );
  } else if (pickerKind === 'theme') {
    pickerNode = (
      <ThemePicker
        onDone={(setting) => {
          setPickerKind(null);
          pushSystem(`Theme set to ${setting}.`);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  } else if (pickerKind === 'resume') {
    // Channel-bound sessions (source="wechat"/"telegram"/…) bubble to
    // the top with a [channel:peer] tag prefix so users can pick a
    // wechat conversation directly without scanning random IDs.
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
    pickerNode = (
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

  // Layout — main-buffer (no alt-screen). Each render appends at the
  // terminal cursor; old renders scroll into terminal scrollback
  // where the user's native ⌘↑ / wheel picks them up. No ScrollBox,
  // no overflow tricks, no flex-shrink — content takes natural
  // height. Welcome shows on a fresh empty session only; once the
  // transcript has anything in it (resume, first reply), Welcome
  // disappears so it doesn't re-print every render and cycle into
  // scrollback redundantly.
  return (
    <Box flexDirection="column">
      <Messages
        committed={committed}
        streaming={streaming}
        welcome={pickerNode ? undefined : (stats ?? undefined)}
      />
      {activity ? (
        <Spinner
          verb={activity.verb}
          detail={
            streamRate !== undefined
              ? `${streamRate} chars/s${activity.detail ? ` · ${activity.detail}` : ''}`
              : activity.detail
          }
          elapsed={elapsed}
        />
      ) : null}
      {pickerNode ? (
        pickerNode
      ) : (
        <PromptInput
          onSubmit={onSubmit}
          busy={!!activity}
          onSlashModeChange={setSlashMode}
          onCancel={onCancel}
          history={history}
        />
      )}
      <BottomBar
          agent={agent}
          model={model}
          conversationId={conversationId}
          conversationTitle={conversationTitle}
          busy={!!activity}
          slashMode={slashMode}
          tokens={conversationId ? tokensByConv[conversationId] : undefined}
          toolsOn={toolsOn}
          permissionMode={permissionMode}
          thinkingEffort={thinkingEffort}
          connState={connState}
          contextWindow={conversationId ? windowByConv[conversationId] : undefined}
          exitPending={exitPending}
        />
    </Box>
  );
};
