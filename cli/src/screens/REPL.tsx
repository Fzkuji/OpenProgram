import React, { useEffect, useState, useRef } from 'react';
import { writeFileSync } from 'fs';
import { join } from 'path';
import { Box, useApp, useInput } from 'ink';
import { BackendClient, WsEnvelope, StatsEnvelope, ConnectionState } from '../ws/client.js';
import { BottomBar } from '../components/BottomBar.js';
import { Messages } from '../components/Messages.js';
import { Spinner } from '../components/Spinner.js';
import { Picker, PickerItem } from '../components/Picker.js';
import { Turn, ToolCall } from '../components/Turn.js';
import { PromptInput } from '../components/PromptInput/PromptInput.js';
import { handleSlash } from '../commands/handler.js';
import { loadHistory, appendHistory, trimHistoryFile } from '../utils/history.js';
import { copyToClipboard } from '../utils/clipboard.js';
import { useTerminalWidth, useTerminalHeight } from '../utils/useTerminalWidth.js';

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
    Array<{ id?: string; title?: string; created_at?: number }>
  >([]);
  const [pickerKind, setPickerKind] = useState<
    null | 'model' | 'resume' | 'agent' | 'channel' | 'channel_account'
  >(null);
  const [channelAccounts, setChannelAccounts] = useState<
    Array<{ channel?: string; account_id?: string; configured?: boolean }>
  >([]);
  const [chosenChannel, setChosenChannel] = useState<string | undefined>(undefined);
  const [agentsList, setAgentsList] = useState<AgentInfo[]>([]);
  const [toolsOn, setToolsOn] = useState(true);
  // Permission cycle: ask before each tool call → auto-approve safe → bypass everything.
  const [permissionMode, setPermissionMode] = useState<'ask' | 'auto' | 'bypass'>('ask');
  // Thinking effort cycle: off → low → medium → high → off.
  const [thinkingEffort, setThinkingEffort] = useState<'off' | 'low' | 'medium' | 'high'>('medium');
  const [connState, setConnState] = useState<ConnectionState>(client.getState());
  const agentSetRef = useRef(false);
  // Bumps every time the terminal resizes so Messages can remount the
  // <Static> region and re-print all committed turns at the new width
  // (after our resize-time clear-screen).
  const cols = useTerminalWidth();
  const rows = useTerminalHeight();
  const lastSizeRef = useRef<string>(`${cols}x${rows}`);
  const [resizeNonce, setResizeNonce] = useState(0);
  useEffect(() => {
    const sig = `${cols}x${rows}`;
    if (sig !== lastSizeRef.current) {
      lastSizeRef.current = sig;
      setResizeNonce((n) => n + 1);
    }
  }, [cols, rows]);

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

  const upsertStreamingText = (delta: string) => {
    setStreaming((s) => ({
      id: s?.id ?? `a-${Date.now()}`,
      role: 'assistant',
      text: (s?.text ?? '') + delta,
      tools: s?.tools ?? [],
      streaming: true,
    }));
  };

  const appendStreamingTool = (tool: string, input?: string) => {
    setStreaming((s) => {
      const base: Turn = s ?? {
        id: `a-${Date.now()}`,
        role: 'assistant',
        text: '',
        tools: [],
        streaming: true,
      };
      const tools = base.tools ?? [];
      const callId = `t-${Date.now()}-${tools.length}`;
      const call: ToolCall = { id: callId, tool, input, status: 'running' };
      return { ...base, tools: [...tools, call], streaming: true };
    });
  };

  const finalizeStreamingTools = () => {
    setStreaming((s) => {
      if (!s || !s.tools || s.tools.length === 0) return s;
      const tools = s.tools.map((t) =>
        t.status === 'running' ? { ...t, status: 'done' as const } : t,
      );
      return { ...s, tools };
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
            // Attach the result preview to the most recent matching call.
            setStreaming((s) => {
              if (!s) return s;
              const tools = (s.tools ?? []).slice();
              for (let i = tools.length - 1; i >= 0; i--) {
                if (tools[i]?.tool === innerWithResult.tool && tools[i]?.status === 'running') {
                  tools[i] = {
                    ...tools[i]!,
                    status: innerWithResult.is_error ? 'error' : 'done',
                    result: innerWithResult.result,
                  };
                  break;
                }
              }
              return { ...s, tools };
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
            const tools = s?.tools ?? [];
            const final: Turn = {
              id: s?.id ?? `a-${Date.now()}`,
              role: 'assistant',
              text,
              tools,
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
      } else if (ev.type === 'history_list') {
        setPastConversations(ev.data ?? []);
      } else if (ev.type === 'conversations_list') {
        const data = ev.data ?? [];
        const lines = data.length === 0
          ? ['(no past sessions)']
          : data.slice(0, 20).map((c: { id?: string; title?: string }) =>
              `  ${c.id?.slice(0, 18) ?? '?'}  ${c.title ?? ''}`,
            );
        pushSystem(`Sessions:\n${lines.join('\n')}`);
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
        };
        if (data.id) setConversationId(data.id);
        if (data.title) setConversationTitle(data.title);
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

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      app.exit();
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
  });

  const onSubmit = (text: string) => {
    if (!text.trim()) return;
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
      });
      if (handled) return;
    }
    setCommitted((m) => [...m, { id: `u-${Date.now()}`, role: 'user', text }]);
    if (!conversationTitle && committed.length === 0) {
      // Mirror server-side behaviour: first user message becomes the title.
      setConversationTitle(text.slice(0, 50) + (text.length > 50 ? '…' : ''));
    }
    setHistory((h) => {
      if (h[h.length - 1] === text) return h;
      appendHistory(text);
      return [...h, text].slice(-500);
    });
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
    const items: PickerItem<string>[] = filtered.length === 0
      ? [{ label: '(no accounts — run shell login first)', description: '', value: '' }]
      : filtered.map((a) => ({
          label: a.account_id ?? '',
          description: a.configured ? 'logged in' : 'not configured',
          value: a.account_id ?? '',
        }));
    pickerNode = (
      <Picker
        title={`Pick a ${chosenChannel} account`}
        items={items}
        onSelect={(it) => {
          if (!it.value) {
            pushSystem(
              `Run \`openprogram channels accounts ${chosenChannel === 'wechat' ? 'login' : 'add'} ${chosenChannel} default\` from the shell first, then re-open /channel.`,
            );
            setPickerKind(null);
            setChosenChannel(undefined);
            return;
          }
          if (!conversationId) {
            pushSystem('Send a message first to create a session, then attach.');
            setPickerKind(null);
            setChosenChannel(undefined);
            return;
          }
          pushSystem(
            `Selected ${chosenChannel}:${it.value}.\n` +
            `Now type \`/attach ${chosenChannel} ${it.value} <peer-id>\` with the channel-side\n` +
            `peer (e.g. wxid_xxx for WeChat) to bind that contact to ${conversationId}.`,
          );
          setPickerKind(null);
          setChosenChannel(undefined);
        }}
        onCancel={() => {
          setPickerKind('channel');
        }}
      />
    );
  } else if (pickerKind === 'resume') {
    const items: PickerItem<string>[] = pastConversations
      .filter((c) => c.id)
      .map((c) => ({
        label: (c.title || c.id || '').slice(0, 60),
        description: `${c.id ?? ''} · ${tsToDate(c.created_at)}`,
        value: c.id!,
      }));
    pickerNode = (
      <Picker
        title="Resume a session"
        items={items}
        onSelect={(it) => {
          client.send({ action: 'load_conversation', id: it.value });
          setConversationId(it.value);
          setCommitted([]);
          setStreaming(null);
          setPickerKind(null);
        }}
        onCancel={() => setPickerKind(null)}
      />
    );
  }

  return (
    <Box flexDirection="column">
      <Messages
        committed={committed}
        streaming={streaming}
        welcome={stats ? stats : undefined}
        resizeNonce={resizeNonce}
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
      />
    </Box>
  );
};
