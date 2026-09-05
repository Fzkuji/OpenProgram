import { describe, expect, it, vi } from 'vitest';

import { handleChatResponse } from '../src/screens/repl/wsHandlers/handleChatResponse.js';
import {
  handleRunningTaskEnvelope,
  type WsEventsCtx,
} from '../src/screens/repl/useWsEvents.js';
import type { Turn } from '../src/components/Turn.js';


describe('handleChatResponse', () => {
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
