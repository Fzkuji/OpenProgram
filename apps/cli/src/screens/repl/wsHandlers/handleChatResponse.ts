/**
 * chat_response WS envelope handler.
 *
 * The dispatcher emits chat_response for every step of an agent turn
 * — text deltas, tool start/end, status, the final result, and
 * context_stats. We split routing into two paths:
 *
 *   1. Foreign conv (data.session_id !== current TUI conv): redirect
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
import { backendBase, backendFetch } from '../../../utils/backend.js';
import {
  streamText,
  upsertStreamingText,
  appendStreamingTool,
  finalizeStreamingTools,
  completeToolBlocks,
} from './streamingHelpers.js';
import type { WsEventsCtx } from '../useWsEvents.js';

interface ChatResponseData {
  type?: string;
  session_id?: string;
  /** The branch the turn runs on. */
  branch_id?: string;
  content?: string;
  event?: {
    type?: string;
    text?: string;
    /** text: the blank line the reply's flat text takes before this delta,
     *  on the frame that opens a passage after a tool call. */
    separator?: string;
    tool?: string;
    input?: string;
    result?: string;
    is_error?: boolean;
    tool_call_id?: string | null;
    full_output?: { total_lines?: number | null } | null;
    attempt?: number;
    next_attempt?: number;
    mode?: 'native' | 'tool' | 'prompt';
    value?: unknown;
    issues?: Array<Record<string, unknown>>;
  };
  structured_output?: unknown;
  structured_output_mode?: 'native' | 'tool' | 'prompt';
  attempt?: number;
  code?: string;
  attempts?: number;
  issues?: Array<Record<string, unknown>>;
  chat?: { input_tokens?: number; output_tokens?: number };
  context_window?: number | null;
  model?: string;
  error?: string | null;
  kind?: string;
  no_op?: boolean;
  user_initiated?: boolean;
  cancelled?: boolean;
  tokens_before?: number | null;
  tokens_after?: number | null;
  summarised_count?: number | null;
  /** The foreground operation a /compact runs as (`operation.cancel`). */
  op_id?: string;
}

/** The branch each conversation shows, from `session_loaded` and
 *  `branch_checked_out`. Every branch can run at once and a switch never
 *  waits for a turn; only the selected branch's turn is drawn. */
const selectedBranchByConv = new Map<string, string>();

export function selectBranch(conversationId: string, branchId: unknown): void {
  if (typeof branchId === 'string' && branchId) selectedBranchByConv.set(conversationId, branchId);
}

/** A frame of a turn on another branch than the one shown. */
export function offSelectedBranch(d: { session_id?: string; branch_id?: unknown }, conversationId?: string): boolean {
  const sid = d.session_id ?? conversationId;
  const selected = sid ? selectedBranchByConv.get(sid) : undefined;
  return typeof d.branch_id === 'string' && !!d.branch_id && !!selected && d.branch_id !== selected;
}

/** The /compact running on each conversation, by its operation id, so the
 *  stop key can name it while no turn streams. */
const compactionOpByConv = new Map<string, string>();

export function compactionOperation(conversationId: string | undefined): string | undefined {
  return conversationId ? compactionOpByConv.get(conversationId) : undefined;
}

const tokens = (n: number | null | undefined): string | null =>
  typeof n === 'number' ? (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)) : null;

/** The line a compaction's end leaves in the transcript. */
export function compactionResultText(d: ChatResponseData): string {
  if (d.type === 'compaction_failed') {
    if (d.code === 'run_active') return d.error || 'Compaction refused: the branch is busy.';
    if (d.cancelled) return 'Compaction stopped; context unchanged.';
    return `Compaction failed${d.error ? `: ${d.error}` : '.'}`;
  }
  if (d.no_op) return 'Nothing to compact.';
  const before = tokens(d.tokens_before);
  const after = tokens(d.tokens_after);
  const covered = typeof d.summarised_count === 'number' ? `, ${d.summarised_count} messages summarized` : '';
  return before && after
    ? `Context compacted: ${before} → ${after} tokens${covered}.`
    : `Context compacted${covered}.`;
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
    markSessionLive(d.session_id ?? c.conversationId);
  }

  // Foreign conv routing.
  const dConvId = d.session_id;
  if (dConvId && c.conversationId && dConvId !== c.conversationId) {
    routeForeignConv(d, dConvId, c);
    return;
  }
  // The branch the person switched away from keeps running out of view.
  if (offSelectedBranch(d, c.conversationId)) return;

  if (d.type === 'stream_event') {
    handleStreamEvent(d, c);
    return;
  }
  if (d.type === 'result' && typeof d.content === 'string') {
    handleResult(d.content, c);
    return;
  }
  if (d.type === 'local_command' && typeof d.content === 'string') {
    c.setStreaming(null);
    c.setCommitted((m) => [
      ...m,
      { id: `local-${Date.now()}`, role: 'system', text: d.content as string },
    ]);
    c.finishTurn();
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
    return;
  }
  if (d.type === 'compaction_started') {
    // /compact or a turn's own compaction: the spinner names the step.
    const sid = d.session_id ?? c.conversationId;
    if (d.user_initiated && d.op_id && sid) compactionOpByConv.set(sid, d.op_id);
    c.setActivity((a) => (
      a ? { ...a, verb: 'Compacting context' } : { verb: 'Compacting context', startedAt: Date.now() }
    ));
    return;
  }
  if (d.type === 'compaction_finished' || d.type === 'compaction_failed') {
    const sid = d.session_id ?? c.conversationId;
    if (d.user_initiated && sid) compactionOpByConv.delete(sid);
    // A turn's own compaction that did not go through is tried again on a
    // later request and leaves the history as it was: nothing to report.
    if (d.user_initiated || d.type === 'compaction_finished') c.pushSystem(compactionResultText(d));
    if (d.user_initiated) {
      // /compact is the branch's whole operation; a turn's own compaction
      // is a step, and the turn keeps its spinner.
      c.setActivity(null);
    } else {
      c.setActivity((a) => (a ? { ...a, verb: 'Thinking' } : a));
    }
  }
}

function routeForeignConv(
  d: ChatResponseData,
  dConvId: string,
  c: WsEventsCtx,
): void {
  if (d.type === 'stream_event') {
    const inner = d.event;
    if (inner?.type === 'structured_output_retry') {
      c.setChannelActivityByConv((m) => {
        const prev = m[dConvId];
        if (!prev) return m;
        return {
          ...m,
          [dConvId]: { ...prev, streamingText: '', lastUpdate: Date.now() },
        };
      });
      return;
    }
    if (inner?.type === 'text' && typeof inner.text === 'string') {
      const delta = streamText(inner);
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
  if (d.type === 'local_command' && typeof d.content === 'string') {
    c.setChannelActivityByConv((m) => ({
      ...m,
      [dConvId]: {
        ...(m[dConvId] ?? { convId: dConvId }),
        finalText: d.content,
        streaming: false,
        lastUpdate: Date.now(),
      },
    }));
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
  if (inner.type === 'structured_output_retry') {
    c.setStreaming(null);
    return;
  }
  if (inner.type === 'tool_result' && inner.tool) {
    // Finish the call this result belongs to (by call id, else the
    // newest running call of that tool).
    const ev = { ...inner, tool: inner.tool };
    c.setStreaming((s) => (s ? { ...s, blocks: completeToolBlocks(s.blocks ?? [], ev) } : s));
    return;
  }
  if (inner.type === 'text' && typeof inner.text === 'string') {
    const delta = inner.text;
    upsertStreamingText(c.setStreaming, delta, inner.separator ?? '');
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
    appendStreamingTool(c.setStreaming, inner.tool, inner.input, inner.tool_call_id ?? undefined);
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
  // Server tags every context_stats with the session_id it belongs to.
  // Stash by id so switching branches flips the displayed numbers
  // without losing the others.
  const cid = d.session_id ?? c.conversationId;
  if (cid && d.chat) {
    c.setTokensByConv((m) => ({
      ...m,
      [cid]: { input: d.chat!.input_tokens, output: d.chat!.output_tokens },
    }));
  }
  if (cid && 'context_window' in d) {
    // A frame that names the window field and leaves it empty says the
    // model's window is unknown: the old one is dropped, so the bar shows
    // the count alone instead of a limit that belonged to another model.
    const window = typeof d.context_window === 'number' && d.context_window > 0
      ? d.context_window
      : null;
    c.setWindowByConv((m) => {
      if (window !== null) return m[cid] === window ? m : { ...m, [cid]: window };
      if (!(cid in m)) return m;
      const { [cid]: _unknown, ...rest } = m;
      return rest;
    });
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
    const r = await backendFetch(
      `${backendBase()}/api/sessions/${encodeURIComponent(sessionId)}/tokens`,
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
