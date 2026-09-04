import { describe, it, expect } from 'vitest';
import {
  decisionFromFrame,
  enqueueDecision,
  replyAction,
  rejectAction,
} from '../src/screens/repl/questionDecision.js';
import type { PendingDecision } from '../src/screens/repl/types.js';

const mk = (id: string): PendingDecision => ({
  id, executionId: 'exec_1', waitGeneration: 0, expectedVersion: 1,
  kind: 'ask', prompt: '', options: [], multi: false, allow_custom: true,
});

describe('decisionFromFrame', () => {
  it('maps a full ask frame', () => {
    const d = decisionFromFrame({
      id: 'q1', kind: 'ask', prompt: 'Pick one',
      execution_id: 'exec_1', wait_generation: 3, expected_version: 8,
      options: ['a', 'b'], multi: false, allow_custom: true,
    });
    expect(d).toEqual({
      id: 'q1', executionId: 'exec_1', waitGeneration: 3, expectedVersion: 8, kind: 'ask', prompt: 'Pick one',
      options: ['a', 'b'], multi: false, allow_custom: true,
      detail: undefined, tool: undefined, args: undefined,
    });
  });

  it('defaults kind to ask and allow_custom to true when absent', () => {
    const d = decisionFromFrame({ id: 'q2' });
    expect(d?.kind).toBe('ask');
    expect(d?.allow_custom).toBe(true);
    expect(d?.options).toEqual([]);
    expect(d?.multi).toBe(false);
  });

  it('honours allow_custom=false', () => {
    const d = decisionFromFrame({ id: 'q3', allow_custom: false });
    expect(d?.allow_custom).toBe(false);
  });

  it('carries approval tool + args', () => {
    const d = decisionFromFrame({
      id: 'q4', kind: 'approval', prompt: 'Run it?',
      options: ['allow', 'deny'], tool: 'Bash', args: { cmd: 'rm -rf /' },
    });
    expect(d?.kind).toBe('approval');
    expect(d?.tool).toBe('Bash');
    expect(d?.args).toEqual({ cmd: 'rm -rf /' });
  });

  it('returns null for a frame with no id', () => {
    expect(decisionFromFrame({} as never)).toBeNull();
    expect(decisionFromFrame(undefined)).toBeNull();
  });

  it('coerces non-string options to strings', () => {
    const d = decisionFromFrame({ id: 'q5', options: [1, 2] as never });
    expect(d?.options).toEqual(['1', '2']);
  });
});

describe('enqueueDecision', () => {
  it('appends a new decision', () => {
    expect(enqueueDecision([], mk('a'))).toHaveLength(1);
    expect(enqueueDecision([mk('a')], mk('b')).map((d) => d.id)).toEqual(['a', 'b']);
  });

  it('dedupes by id (reconnect replay safe)', () => {
    const q = [mk('a')];
    expect(enqueueDecision(q, mk('a'))).toBe(q); // same ref, no change
  });
});

describe('replyAction / rejectAction', () => {
  it('reply carries the canonical execution envelope', () => {
    expect(replyAction(mk('q1'), 'luxon')).toMatchObject({
      type: 'execution.command', action: 'execution.wait.answer',
      execution_id: 'exec_1', expected_version: 1,
      payload: { wait_id: 'q1', generation: 0, answer: 'luxon' },
    });
  });

  it('reply carries an array answer for multi', () => {
    expect(replyAction(mk('q1'), ['a', 'b'])).toMatchObject({
      action: 'execution.wait.answer', payload: { wait_id: 'q1', generation: 0, answer: ['a', 'b'] },
    });
  });

  it('reject omits reason when none given', () => {
    expect(rejectAction(mk('q1'))).toMatchObject({
      action: 'execution.wait.decline', payload: { wait_id: 'q1', generation: 0 },
    });
  });

  it('reject carries a reason when given', () => {
    expect(rejectAction(mk('q1'), 'too risky')).toMatchObject({
      action: 'execution.wait.decline', payload: { wait_id: 'q1', generation: 0, reason: 'too risky' },
    });
  });
});
