import { describe, expect, it } from 'vitest';

import {
  INITIAL_STATE,
  parseMultipleKeypresses,
} from '../src/runtime/ink/parse-keypress.js';

describe('batched terminal input', () => {
  it('preserves printable characters followed by enter', () => {
    const [keys] = parseMultipleKeypresses(INITIAL_STATE, '/help\r');

    expect(keys.map((key) => key.sequence)).toEqual(['/help', '\r']);
  });

  it('normalizes a coalesced Windows CRLF to one Enter key', () => {
    const [keys] = parseMultipleKeypresses(INITIAL_STATE, 'hello\r\n');

    expect(keys.map((key) => key.sequence)).toEqual(['hello', '\r']);
  });

  it.each(['中', '🙂'])('decodes split UTF-8 buffers without replacement characters: %s', (value) => {
    const encoded = Buffer.from(value, 'utf8');

    for (let split = 1; split < encoded.length; split++) {
      const [before, state] = parseMultipleKeypresses(
        INITIAL_STATE,
        encoded.subarray(0, split),
      );
      const [after] = parseMultipleKeypresses(
        state,
        encoded.subarray(split),
      );

      expect(before).toEqual([]);
      expect(after.map((key) => key.sequence)).toEqual([value]);
      expect(after.some((key) => key.sequence?.includes('\ufffd'))).toBe(false);
    }
  });
});
