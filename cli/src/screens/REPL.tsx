import React, { useEffect, useState, useRef } from 'react';
import { Box, useApp, useInput } from 'ink';
import { BackendClient, WsEnvelope, StatsEnvelope } from '../ws/client.js';
import { BottomBar } from '../components/BottomBar.js';
import { Messages } from '../components/Messages.js';
import { Spinner } from '../components/Spinner.js';
import { Picker, PickerItem } from '../components/Picker.js';
import { Turn, ToolCall } from '../components/Turn.js';
import { PromptInput } from '../components/PromptInput/PromptInput.js';
import { handleSlash } from '../commands/handler.js';

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
  const [tokens, setTokens] = useState<{ input?: number; output?: number }>({});
  const [history, setHistory] = useState<string[]>([]);
  const [modelsList, setModelsList] = useState<string[]>([]);
  const [pastConversations, setPastConversations] = useState<
    Array<{ id?: string; title?: string; created_at?: number }>
  >([]);
  const [pickerKind, setPickerKind] = useState<null | 'model' | 'resume' | 'agent'>(
    null,
  );
  const [agentsList, setAgentsList] = useState<AgentInfo[]>([]);
  const [toolsOn, setToolsOn] = useState(false);
  const agentSetRef = useRef(false);

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
    }));
  };

  const appendStreamingTool = (tool: string, input?: string) => {
    setStreaming((s) => {
      const base: Turn = s ?? {
        id: `a-${Date.now()}`,
        role: 'assistant',
        text: '',
        tools: [],
      };
      const tools = base.tools ?? [];
      const callId = `t-${Date.now()}-${tools.length}`;
      const call: ToolCall = { id: callId, tool, input, status: 'running' };
      return { ...base, tools: [...tools, call] };
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
          if (inner.type === 'text' && typeof inner.text === 'string') {
            upsertStreamingText(inner.text);
            setActivity((a) => (a ? { ...a, verb: 'Streaming' } : a));
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
          finishTurn();
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
          const chat = (d as { chat?: { input?: number; output?: number } }).chat;
          if (chat) setTokens({ input: chat.input, output: chat.output });
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
      } else if (ev.type === 'history_list') {
        setPastConversations(ev.data ?? []);
      } else if (ev.type === 'conversation_loaded') {
        const data = ev.data as {
          id?: string;
          messages?: Array<{ role?: string; content?: string }>;
        };
        if (data.id) setConversationId(data.id);
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
    client.send({ action: 'stats' });
    client.send({ action: 'list_agents' });
    return () => {
      off();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      app.exit();
      return;
    }
    // shift+tab toggles tool availability for the next turn (Claude Code parity).
    if (key.shift && key.tab) {
      setToolsOn((on) => !on);
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
        currentAgent: agent,
        currentModel: model,
        currentConversation: conversationId,
      });
      if (handled) return;
    }
    setCommitted((m) => [...m, { id: `u-${Date.now()}`, role: 'user', text }]);
    setHistory((h) =>
      h[h.length - 1] === text ? h : [...h, text].slice(-200),
    );
    startTurn('Thinking');
    client.send({
      action: 'chat',
      conv_id: conversationId,
      agent_id: agent,
      text,
      tools: toolsOn,
    });
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
      />
      {activity ? (
        <Spinner verb={activity.verb} detail={activity.detail} elapsed={elapsed} />
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
        busy={!!activity}
        slashMode={slashMode}
        tokens={tokens}
        toolsOn={toolsOn}
      />
    </Box>
  );
};
