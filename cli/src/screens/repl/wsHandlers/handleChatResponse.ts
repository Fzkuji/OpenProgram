/**
 * chat_response WS envelope handler.
 *
 * The dispatcher emits chat_response for every step of an agent turn
 * — text deltas, tool start/end, status, the final result, and
 * context_stats. We split routing into two paths:
 *
 *   1. Foreign conv (data.conv_id !== current TUI conv): redirect
 *      the activity into setChannelActivityByConv so the bottom
 *      ChannelActivityFeed shows live "wechat:bot42 → main:
 *      streaming…" updates without polluting the user's current
 *      transcript.
 *
 *   2. Current conv: original streaming behaviour — text deltas
 *      append to the streaming Turn, tool_use opens a tool block,
 *      tool_result attaches its preview, result finalizes into a
 *      committed Turn.
 */
import { Turn, TurnBlock } from '../../../components/Turn.js';
import { stripProviderPrefix } from '../helpers.js';
import {
  upsertStreamingText,
  appendStreamingTool,
  finalizeStreamingTools,
} from './streamingHelpers.js';
import type { WsEventsCtx } from '../useWsEvents.js';

interface ChatResponseData {
  type?: string;
  conv_id?: string;
  content?: string;
  event?: { type?: string; text?: string; tool?: string; input?: string; result?: string; is_error?: boolean };
  chat?: { input_tokens?: number; output_tokens?: number };
  context_window?: number | null;
  model?: string;
}

export function handleChatResponse(
  d: ChatResponseData,
  c: WsEventsCtx,
  markSessionLive: (convId?: string) => void,
): void {
  if (
    d.type === 'status' ||
    d.type === 'stream_event' ||
    d.type === 'result' ||
    d.type === 'context_stats'
  ) {
    markSessionLive(d.conv_id ?? c.conversationId);
  }

  // Foreign conv routing.
  const dConvId = d.conv_id;
  if (dConvId && c.conversationId && dConvId !== c.conversationId) {
    routeForeignConv(d, dConvId, c);
    return;
  }

  if (d.type === 'stream_event') {
    handleStreamEvent(d, c);
    return;
  }
  if (d.type === 'result' && typeof d.content === 'string') {
    handleResult(d.content, c);
    return;
  }
  if (d.type === 'error' && typeof d.content === 'string') {
    c.setStreaming(null);
    c.setCommitted((m) => [
      ...m,
      { id: `e-${Date.now()}`, role: 'system', text: `error: ${d.content}` },
    ]);
    c.finishTurn();
    return;
  }
  if (d.type === 'status' && typeof d.content === 'string') {
    // Server sends "Thinking..." — fold into the spinner verb so the
    // committed area stays uncluttered.
    c.setActivity((a) =>
      a ? { ...a, verb: (d.content as string).replace(/\.+$/, '') } : a,
    );
    return;
  }
  if (d.type === 'context_stats') {
    handleContextStats(d, c);
  }
}

function routeForeignConv(
  d: ChatResponseData,
  dConvId: string,
  c: WsEventsCtx,
): void {
  if (d.type === 'stream_event') {
    const inner = d.event;
    if (inner?.type === 'text' && typeof inner.text === 'string') {
      const delta = inner.text;
      c.setChannelActivityByConv((m) => {
        const prev = m[dConvId] ?? {
          convId: dConvId,
          streamingText: '',
          streaming: true,
          lastUpdate: Date.now(),
        };
        return {
          ...m,
          [dConvId]: {
            ...prev,
            streamingText: (prev.streamingText ?? '') + delta,
            streaming: true,
            lastUpdate: Date.now(),
          },
        };
      });
    }
    return;
  }
  if (d.type === 'result' && typeof d.content === 'string') {
    const finalText = d.content;
    c.setChannelActivityByConv((m) => {
      const prev = m[dConvId] ?? {
        convId: dConvId,
        streamingText: '',
        streaming: false,
        lastUpdate: Date.now(),
      };
      return {
        ...m,
        [dConvId]: {
          ...prev,
          finalText,
          streaming: false,
          lastUpdate: Date.now(),
        },
      };
    });
    return;
  }
  // status / error for foreign convs — refresh timestamp so stale
  // entries can age out cleanly.
  if (d.type === 'status' || d.type === 'error') {
    c.setChannelActivityByConv((m) => {
      if (!m[dConvId]) return m;
      return { ...m, [dConvId]: { ...m[dConvId], lastUpdate: Date.now() } };
    });
  }
}

function handleStreamEvent(d: ChatResponseData, c: WsEventsCtx): void {
  const inner = d.event;
  if (!inner) return;
  if (inner.type === 'tool_result' && inner.tool) {
    // Attach the result preview to the most recent matching call —
    // search blocks bottom-up for the last 'running' tool block with
    // this tool name and update it in place.
    c.setStreaming((s) => {
      if (!s) return s;
      const blocks = (s.blocks ?? []).slice();
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (
          b?.kind === 'tool'
          && b.call.tool === inner.tool
          && b.call.status === 'running'
        ) {
          blocks[i] = {
            kind: 'tool',
            call: {
              ...b.call,
              status: inner.is_error ? 'error' : 'done',
              result: inner.result,
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
}

function handleResult(text: string, c: WsEventsCtx): void {
  finalizeStreamingTools(c.setStreaming);
  // Ring terminal bell if the turn took long enough that the user
  // might have switched away. 5s threshold matches Claude Code's
  // default. Suppressed via /bell.
  c.setActivity((a) => {
    if (c.bellEnabled && a && Date.now() - a.startedAt > 5000) {
      process.stdout.write('\x07');
    }
    return null;
  });
  c.setStreaming((s) => {
    // Preserve the streamed block sequence so the committed turn
    // renders text + tool calls in the order they actually arrived
    // from the model. Fall back to a single text block if nothing was
    // streamed.
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
    c.setCommitted((m) => [...m, final]);
    return null;
  });
}

function handleContextStats(d: ChatResponseData, c: WsEventsCtx): void {
  // Server tags every context_stats with the conv_id it belongs to.
  // Stash by id so switching branches flips the displayed numbers
  // without losing the others.
  const cid = d.conv_id ?? c.conversationId;
  if (cid && d.chat) {
    c.setTokensByConv((m) => ({
      ...m,
      [cid]: { input: d.chat!.input_tokens, output: d.chat!.output_tokens },
    }));
  }
  if (
    cid
    && typeof d.context_window === 'number'
    && d.context_window > 0
  ) {
    c.setWindowByConv((m) => ({ ...m, [cid]: d.context_window as number }));
  }
  // Live model from the actual runtime. Trumps agent-default values
  // seeded by stats/agents_list — those describe what the agent is
  // configured to use, not what the runtime we're talking to right
  // now actually is.
  if (d.model && cid === c.conversationId) {
    c.setModel(stripProviderPrefix(d.model));
  }
  // Pull branch-level stats (cache hit rate, source mix) from the
  // dedicated REST endpoint. The WS context_stats doesn't carry the
  // cache_read total or the precision-disclosure source_mix, so we
  // round-trip once per turn. Cheap (single-digit ms) and only fires
  // when a turn lands — idle sessions don't poll.
  if (cid) {
    void fetchBranchTokenStats(cid).then((stats) => {
      if (!stats) return;
      c.setTokenStatsByConv((m) => ({ ...m, [cid]: stats }));
    });
  }
}

interface BranchTokenStats {
  current_tokens: number;
  context_window: number;
  cache_hit_rate: number;
  cache_read_total: number;
  source_mix: Record<string, number>;
}

async function fetchBranchTokenStats(
  sessionId: string,
): Promise<BranchTokenStats | null> {
  try {
    const base = process.env.OPENPROGRAM_BACKEND_URL
      || process.env.OPENPROGRAM_WS?.replace('ws://', 'http://').replace('/ws', '')
      || 'http://127.0.0.1:8765';
    const r = await fetch(
      `${base}/api/sessions/${encodeURIComponent(sessionId)}/tokens`,
    );
    if (!r.ok) return null;
    const d = (await r.json()) as Partial<BranchTokenStats>;
    return {
      current_tokens: d.current_tokens || 0,
      context_window: d.context_window || 0,
      cache_hit_rate: d.cache_hit_rate || 0,
      cache_read_total: d.cache_read_total || 0,
      source_mix: d.source_mix || {},
    };
  } catch {
    return null;
  }
}
