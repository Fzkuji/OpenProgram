import { describe, expect, it, vi } from 'vitest';

import { compactionOperation, handleChatResponse, selectBranch } from '../src/screens/repl/wsHandlers/handleChatResponse.js';
import { cancelOperation } from '../src/screens/repl/cancelExecution.js';
import { appendStreamingTool, streamText, upsertStreamingText } from '../src/screens/repl/wsHandlers/streamingHelpers.js';
import {
  handleRunningTaskEnvelope,
  type WsEventsCtx,
} from '../src/screens/repl/useWsEvents.js';
import type { Turn } from '../src/components/Turn.js';


describe('upsertStreamingText', () => {
  it('puts the blank line a passage opens with in the flat text, never in a block', () => {
    let turn: Turn | null = null;
    const setStreaming = ((next: (current: Turn | null) => Turn | null) => { turn = next(turn); }) as never;
    upsertStreamingText(setStreaming, 'before');
    appendStreamingTool(setStreaming, 'read', '{}', 't1');
    upsertStreamingText(setStreaming, 'af', '\n\n');
    upsertStreamingText(setStreaming, 'ter');
    const drawn = turn as unknown as Turn;
    expect(drawn.text).toBe('before\n\nafter');
    expect((drawn.blocks ?? []).filter((block) => block.kind === 'text').map((block) => (block as { text: string }).text))
      .toEqual(['before', 'after']);
    expect(streamText({ text: 'af', separator: '\n\n' })).toBe('\n\naf');
    expect(streamText({ text: 'ter' })).toBe('ter');
  });
});

describe('handleChatResponse', () => {
  it('drops the context window when the server knows none for the model', () => {
    let windows: Record<string, number> = {};
    const ctx = {
      conversationId: 'session-w',
      setTokensByConv: vi.fn(),
      setWindowByConv: (update: (m: Record<string, number>) => Record<string, number>) => {
        windows = update(windows);
      },
      setTokenStatsByConv: vi.fn(),
      setModel: vi.fn(),
    } as unknown as WsEventsCtx;
    const stats = (context_window?: number | null) => ({
      type: 'context_stats',
      session_id: 'session-w',
      chat: { input_tokens: 1200, output_tokens: 30 },
      ...(context_window === undefined ? {} : { context_window }),
    });

    handleChatResponse(stats(200_000) as never, ctx, vi.fn());
    expect(windows).toEqual({ 'session-w': 200_000 });
    // A frame without the field (an exec-only report) keeps the window.
    handleChatResponse(stats() as never, ctx, vi.fn());
    expect(windows).toEqual({ 'session-w': 200_000 });
    // null: the model changed to one whose window is not known.
    handleChatResponse(stats(null) as never, ctx, vi.fn());
    expect(windows).toEqual({});
  });

  it('draws only the selected branch while another branch runs out of view', () => {
    const setStreaming = vi.fn();
    const finishTurn = vi.fn();
    const ctx = {
      conversationId: 'session-b',
      setStreaming,
      setCommitted: vi.fn(),
      finishTurn,
    } as unknown as WsEventsCtx;
    selectBranch('session-b', 'branch_b');

    handleChatResponse(
      { type: 'error', session_id: 'session-b', branch_id: 'branch_a', content: 'left behind' },
      ctx,
      vi.fn(),
    );
    expect(setStreaming).not.toHaveBeenCalled();
    expect(finishTurn).not.toHaveBeenCalled();

    handleChatResponse(
      { type: 'error', session_id: 'session-b', branch_id: 'branch_b', content: 'shown' },
      ctx,
      vi.fn(),
    );
    expect(finishTurn).toHaveBeenCalledOnce();
  });

  it('commits local command output and finishes the turn', () => {
    let committed: Turn[] = [];
    const finishTurn = vi.fn();
    const setStreaming = vi.fn();
    const ctx = {
      conversationId: 'session-1',
      setStreaming,
      setCommitted: (update: (rows: Turn[]) => Turn[]) => {
        committed = update(committed);
      },
      finishTurn,
    } as unknown as WsEventsCtx;

    handleChatResponse(
      { type: 'local_command', session_id: 'session-1', content: 'Add project settings' },
      ctx,
      vi.fn(),
    );

    expect(setStreaming).toHaveBeenCalledWith(null);
    expect(committed).toMatchObject([
      { role: 'system', text: 'Add project settings' },
    ]);
    expect(finishTurn).toHaveBeenCalledOnce();
  });

  it('records local output for a session that is no longer focused', () => {
    let background: Record<string, unknown> = {};
    const ctx = {
      conversationId: 'session-2',
      setChannelActivityByConv: (
        update: (rows: Record<string, unknown>) => Record<string, unknown>,
      ) => {
        background = update(background);
      },
    } as unknown as WsEventsCtx;

    handleChatResponse(
      { type: 'local_command', session_id: 'session-1', content: 'Add project settings' },
      ctx,
      vi.fn(),
    );

    expect(background).toMatchObject({
      'session-1': {
        convId: 'session-1',
        finalText: 'Add project settings',
        streaming: false,
      },
    });
  });

  it('clears the rejected structured-output candidate before retry', () => {
    const setStreaming = vi.fn();
    const ctx = {
      conversationId: 'session-1',
      setStreaming,
      setActivity: vi.fn(),
    } as unknown as WsEventsCtx;

    handleChatResponse(
      {
        type: 'stream_event',
        session_id: 'session-1',
        event: {
          type: 'structured_output_retry',
          attempt: 1,
          next_attempt: 2,
          issues: [],
        },
      },
      ctx,
      vi.fn(),
    );

    expect(setStreaming).toHaveBeenCalledWith(null);
  });
});

describe('handleRunningTaskEnvelope', () => {
  it('does not replace the focused execution with another session', () => {
    const executionIdRef = { current: 'exec-a' };
    const executionVersionRef = { current: 1 };
    const setStreaming = vi.fn();
    const ctx = {
      conversationId: 'session-a',
      executionIdRef,
      executionVersionRef,
      setStreaming,
    } as unknown as WsEventsCtx;

    handleRunningTaskEnvelope({
      type: 'running_task',
      data: { session_id: 'session-b', execution_id: 'exec-b' },
    }, ctx);

    expect(executionIdRef.current).toBe('exec-a');
    expect(setStreaming).not.toHaveBeenCalled();

    handleRunningTaskEnvelope({
      type: 'running_task',
      data: { session_id: 'session-a', execution_id: 'exec-a-next' },
    }, ctx);

    expect(executionIdRef.current).toBe('exec-a-next');
    expect(executionVersionRef.current).toBe(1);
    expect(setStreaming).toHaveBeenCalledOnce();
  });

  it('retains the latest exact version from chat and running frames', () => {
    const executionIdRef = { current: undefined as string | undefined };
    const executionVersionRef = { current: undefined as number | undefined };
    const ctx = {
      conversationId: 'session-a',
      executionIdRef,
      executionVersionRef,
      setStreaming: vi.fn(),
    } as unknown as WsEventsCtx;

    handleRunningTaskEnvelope({
      type: 'running_task',
      data: { session_id: 'session-a', execution_id: 'exec-a', status_version: 7 },
    }, ctx);

    expect(executionIdRef.current).toBe('exec-a');
    expect(executionVersionRef.current).toBe(7);
  });
});

describe('compaction frames', () => {
  const ctxWith = () => {
    const pushed: string[] = [];
    let activity: { verb: string; startedAt: number } | null = null;
    const ctx = {
      conversationId: 's',
      pushSystem: (text: string) => pushed.push(text),
      setActivity: (update: unknown) => {
        activity = typeof update === 'function'
          ? (update as (a: typeof activity) => typeof activity)(activity)
          : (update as typeof activity);
      },
    } as unknown as WsEventsCtx;
    return { ctx, pushed, activity: () => activity };
  };

  it('/compact shows progress, then its result, and ends the spinner', () => {
    const { ctx, pushed, activity } = ctxWith();
    handleChatResponse({ type: 'compaction_started', session_id: 's' }, ctx, vi.fn());
    expect(activity()?.verb).toBe('Compacting context');
    handleChatResponse({
      type: 'compaction_finished', session_id: 's', user_initiated: true,
      tokens_before: 52_000, tokens_after: 8_000, summarised_count: 40,
    }, ctx, vi.fn());
    expect(pushed).toEqual(['Context compacted: 52.0k → 8.0k tokens, 40 messages summarized.']);
    expect(activity()).toBeNull();
  });

  it('/compact names its operation so the stop key can end it, and says it stopped', () => {
    const { ctx, pushed, activity } = ctxWith();
    handleChatResponse({
      type: 'compaction_started', session_id: 's', user_initiated: true, op_id: 'op-7',
    }, ctx, vi.fn());
    expect(compactionOperation('s')).toBe('op-7');
    const sent: unknown[] = [];
    cancelOperation({ send: (req) => { sent.push(req); } }, 's', compactionOperation('s')!);
    expect(sent).toEqual([{ action: 'operation.cancel', session_id: 's', op_id: 'op-7' }]);
    handleChatResponse({
      type: 'compaction_failed', session_id: 's', user_initiated: true, cancelled: true,
      error: 'Compaction cancelled; history unchanged',
    }, ctx, vi.fn());
    expect(compactionOperation('s')).toBeUndefined();
    expect(pushed).toEqual(['Compaction stopped; context unchanged.']);
    expect(activity()).toBeNull();
  });

  it('a refused or failed compaction says why', () => {
    const { ctx, pushed } = ctxWith();
    handleChatResponse({
      type: 'compaction_failed', session_id: 's', user_initiated: true, code: 'run_active',
      kind: 'reply', error: 'A reply is being written on this branch; try again after it ends.',
    }, ctx, vi.fn());
    // A turn's own compaction that fails says nothing: the history is
    // unchanged and a later request tries again.
    handleChatResponse({ type: 'compaction_failed', session_id: 's', error: 'timeout' }, ctx, vi.fn());
    expect(pushed).toEqual([
      'A reply is being written on this branch; try again after it ends.',
    ]);
  });
});
