import { describe, it, expect } from 'vitest';
import { findAtToken } from '../src/utils/fileCompletions.js';

describe('findAtToken', () => {
  it('returns null when there is no @ before cursor', () => {
    expect(findAtToken('hello world', 5)).toBeNull();
  });

  it('extracts the partial after the @ at cursor', () => {
    const v = 'open @src/scre';
    const r = findAtToken(v, v.length);
    expect(r).toEqual({ start: 5, partial: 'src/scre' });
  });

  it('rejects @ embedded in a word (email-like)', () => {
    const v = 'mail me at foo@bar.com';
    const r = findAtToken(v, v.length);
    expect(r).toBeNull();
  });

  it('handles @ as the cursor position itself', () => {
    const v = 'check @';
    const r = findAtToken(v, v.length);
    expect(r).toEqual({ start: 6, partial: '' });
  });

  it('terminates at whitespace before cursor', () => {
    const v = 'pre @aa bb';
    // Cursor after "bb" — there's whitespace between @aa and bb so we are
    // no longer inside the @-token.
    const r = findAtToken(v, v.length);
    expect(r).toBeNull();
  });
});
