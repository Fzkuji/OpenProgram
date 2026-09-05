import { describe, expect, it } from 'vitest';

import { buildInputViewport } from '../src/components/PromptInput/PromptInput.js';
import {
  nextGraphemeBoundary,
  previousGraphemeBoundary,
} from '../src/runtime/utils/intl.js';

describe('prompt Unicode editing', () => {
  const emoji = '👩‍💻';
  const combining = 'e\u0301';
  const value = `A${emoji}${combining}中`;
  const emojiStart = 1;
  const emojiEnd = emojiStart + emoji.length;
  const combiningEnd = emojiEnd + combining.length;

  it('moves by grapheme clusters instead of UTF-16 code units', () => {
    expect(nextGraphemeBoundary(value, emojiStart)).toBe(emojiEnd);
    expect(previousGraphemeBoundary(value, emojiEnd)).toBe(emojiStart);
    expect(nextGraphemeBoundary(value, emojiEnd)).toBe(combiningEnd);
    expect(previousGraphemeBoundary(value, combiningEnd)).toBe(emojiEnd);
  });

  it('renders the complete grapheme beneath the native IME cursor', () => {
    const emojiCursor = buildInputViewport(value, emojiStart, 40);
    expect(emojiCursor.cursor).toBe(emoji);
    expect(emojiCursor.after).toBe(`${combining}中`);

    const combiningCursor = buildInputViewport(value, emojiEnd, 40);
    expect(combiningCursor.cursor).toBe(combining);
    expect(combiningCursor.after).toBe('中');
  });
});
