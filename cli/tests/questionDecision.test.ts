import { describe, it, expect } from 'vitest';
import {
  decisionFromFrame,
  enqueueDecision,
  replyAction,
  rejectAction,
} from '../src/screens/repl/questionDecision.js';
import type { PendingDecision } from '../src/screens/repl/types.js';

describe('decisionFromFrame', () => {
  it('maps a full ask frame', () => {
    const d = decisionFromFrame({
      id: 'q1', kind: 'ask', prompt: 'Pick one',
      options: ['a', 'b'], multi: false, allow_custom: true,
    });
    expect(d).toEqual({
      id: 'q1', kind: 'ask', prompt: 'Pick one',
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
  const mk = (id: string): PendingDecision => ({
    id, kind: 'ask', prompt: '', options: [], multi: false, allow_custom: true,
  });

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
  it('reply carries id + answer (string)', () => {
    expect(replyAction('q1', 'luxon')).toEqual({
      action: 'question_reply', id: 'q1', answer: 'luxon',
    });
  });

  it('reply carries an array answer for multi', () => {
    expect(replyAction('q1', ['a', 'b'])).toEqual({
      action: 'question_reply', id: 'q1', answer: ['a', 'b'],
    });
  });

  it('reject omits reason when none given', () => {
    expect(rejectAction('q1')).toEqual({ action: 'question_reject', id: 'q1' });
  });

  it('reject carries a reason when given', () => {
    expect(rejectAction('q1', 'too risky')).toEqual({
      action: 'question_reject', id: 'q1', reason: 'too risky',
    });
  });
});
