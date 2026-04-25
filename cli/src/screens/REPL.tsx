import React, { useEffect, useState, useRef } from 'react';
import { Box, useApp, useInput } from 'ink';
import { BackendClient, WsEnvelope } from '../ws/client.js';
import { StatusLine } from '../components/StatusLine.js';
import { Messages } from '../components/Messages.js';
import { Welcome } from '../components/Welcome.js';
import { PromptInput } from '../components/PromptInput/PromptInput.js';

export interface REPLProps {
  client: BackendClient;
  initialAgent?: string;
  initialConversation?: string;
}

export interface UIMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;
  tag?: string;
}

interface AgentInfo {
  id: string;
  name: string;
  model?: string | { provider?: string; id?: string };
  default?: boolean;
}

const renderModel = (m: AgentInfo['model']): string | undefined => {
  if (!m) return undefined;
  if (typeof m === 'string') return m;
  return m.id ?? m.provider;
};

export const REPL: React.FC<REPLProps> = ({ client, initialAgent, initialConversation }) => {
  const app = useApp();
  const [committed, setCommitted] = useState<UIMessage[]>([]);
  const [streaming, setStreaming] = useState<UIMessage | null>(null);
  const [agent, setAgent] = useState<string | undefined>(initialAgent);
  const [model, setModel] = useState<string | undefined>(undefined);
  const [conversationId, setConversationId] = useState<string | undefined>(initialConversation);
  const [busy, setBusy] = useState(false);
  const agentSetRef = useRef(false);

  useEffect(() => {
    const off = client.on((ev: WsEnvelope) => {
      if (ev.type === 'chat_ack') {
        setConversationId(ev.data.conv_id);
      } else if (ev.type === 'chat_response') {
        const d = ev.data;
        if (d.type === 'stream_event' && typeof d.content === 'string') {
          setStreaming((s) => ({
            id: s?.id ?? `a-${Date.now()}`,
            role: 'assistant',
            text: (s?.text ?? '') + (d.content as string),
          }));
        } else if (d.type === 'result' && typeof d.content === 'string') {
          const text = d.content as string;
          setStreaming(null);
          setCommitted((m) => [...m, { id: `a-${Date.now()}`, role: 'assistant', text }]);
          setBusy(false);
        } else if (d.type === 'error' && typeof d.content === 'string') {
          setStreaming(null);
          setCommitted((m) => [
            ...m,
            { id: `e-${Date.now()}`, role: 'system', text: `error: ${d.content as string}` },
          ]);
          setBusy(false);
        } else if (d.type === 'status' && typeof d.content === 'string') {
          // Skip noisy "Thinking..." status — busy indicator already covers it.
          if (d.content !== 'Thinking...') {
            setCommitted((m) => [
              ...m,
              { id: `s-${Date.now()}`, role: 'system', text: d.content as string },
            ]);
          }
        }
      } else if (ev.type === 'agents_list') {
        const list = ev.data as AgentInfo[];
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
        }
      } else if (ev.type === 'error') {
        const data = (ev as { data?: { message?: string } }).data;
        const msg = data?.message ?? 'unknown error';
        setCommitted((m) => [...m, { id: `e-${Date.now()}`, role: 'system', text: `error: ${msg}` }]);
      }
    });
    client.send({ action: 'sync' });
    client.send({ action: 'list_agents' });
    return () => {
      off();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      app.exit();
    }
  });

  const onSubmit = (text: string) => {
    if (!text.trim()) return;
    setCommitted((m) => [...m, { id: `u-${Date.now()}`, role: 'user', text }]);
    setBusy(true);
    client.send({
      action: 'chat',
      conv_id: conversationId,
      agent_id: agent,
      text,
    });
  };

  return (
    <Box flexDirection="column">
      {committed.length === 0 && !streaming ? <Welcome /> : null}
      <Messages committed={committed} streaming={streaming} />
      <PromptInput onSubmit={onSubmit} busy={busy} />
      <StatusLine agent={agent} model={model} conversationId={conversationId ?? '(new)'} busy={busy} />
    </Box>
  );
};
