import { describe, expect, it, vi } from 'vitest';

import { handleOperationErrorEnvelope } from '../src/screens/repl/useWsEvents.js';
import type { WsEnvelope } from '../src/ws/client.js';


describe('operation_error dispatch', () => {
  it('shows one low-sensitivity failure without clearing unrelated turn state', () => {
    let turns: Array<{ text: string }> = [];
    const finishTurn = vi.fn();
    const ctx = {
      conversationId: 'session-1',
      setCommitted: (next: unknown) => {
        turns = typeof next === 'function'
          ? (next as (previous: never[]) => never[])(turns as never)
          : next as never;
      },
      finishTurn,
    };
    const handled = handleOperationErrorEnvelope({
      type: 'operation_error',
      data: {
        action: 'set_setting',
        code: 'handler_error',
        message: 'secret=/private/credential',
      },
    }, ctx);

    expect(handled).toBe(true);
    expect(turns).toHaveLength(1);
    expect(turns[0]?.text).toBe('error: action set_setting failed');
    expect(JSON.stringify(turns)).not.toContain('credential');
    expect(finishTurn).not.toHaveBeenCalled();
  });

  it('rejects unsafe action metadata and ignores unrelated frames', () => {
    let turns: Array<{ text: string }> = [];
    const ctx = {
      conversationId: 'session-b',
      setCommitted: (next: unknown) => {
        turns = typeof next === 'function'
          ? (next as (previous: never[]) => never[])(turns as never)
          : next as never;
      },
      finishTurn: vi.fn(),
    };
    expect(handleOperationErrorEnvelope({ type: 'pong' } as WsEnvelope, ctx as never)).toBe(false);
    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: { action: 'line\nbreak', code: 'unknown_action' },
    }, ctx as never)).toBe(true);

    expect(turns[0]?.text).toBe('error: unknown action ?');

    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: {
        action: 'set_setting',
        session_id: 'session-a',
        code: 'handler_error',
      },
    }, ctx as never)).toBe(true);
    expect(turns).toHaveLength(1);
    expect(ctx.finishTurn).not.toHaveBeenCalled();
  });

  it('consumes legacy action errors and ends only the matching chat turn', () => {
    let turns: Array<{ text: string }> = [];
    const finishTurn = vi.fn();
    const ctx = {
      conversationId: 'session-1',
      setCommitted: (next: unknown) => {
        turns = typeof next === 'function'
          ? (next as (previous: never[]) => never[])(turns as never)
          : next as never;
      },
      finishTurn,
    };

    expect(handleOperationErrorEnvelope({
      type: 'action_error',
      data: { action: 'missing_handler', message: 'private detail' },
    }, ctx)).toBe(true);
    expect(turns[0]?.text).toBe('error: unknown action missing_handler');
    expect(JSON.stringify(turns)).not.toContain('private detail');
    expect(finishTurn).not.toHaveBeenCalled();

    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: {
        action: 'chat',
        session_id: 'session-1',
        code: 'handler_error',
      },
    }, ctx)).toBe(true);
    expect(turns[1]?.text).toBe('error: action chat failed');
    expect(finishTurn).toHaveBeenCalledTimes(1);

    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: { action: 'chat', code: 'handler_error' },
    }, ctx)).toBe(true);
    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: {
        action: 'chat',
        session_id: 'unsafe\nsession',
        code: 'handler_error',
      },
    }, ctx)).toBe(true);
    expect(finishTurn).toHaveBeenCalledTimes(1);

    ctx.conversationId = undefined;
    expect(handleOperationErrorEnvelope({
      type: 'operation_error',
      data: { action: 'chat', code: 'handler_error' },
    }, ctx)).toBe(true);
    expect(finishTurn).toHaveBeenCalledTimes(2);
  });
});
