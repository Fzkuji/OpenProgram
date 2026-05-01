import { describe, expect, it } from 'vitest';
import { getColumnOverflow, getWelcomeLayout } from '../src/components/Welcome.js';

describe('Welcome layout height policy', () => {
  it('keeps the opening panel visible as terminal height decreases', () => {
    expect(getWelcomeLayout(36, true)).toEqual({
      mode: 'two-rows-items',
      itemsPerTile: 12,
    });
    expect(getWelcomeLayout(19, true)).toEqual({
      mode: 'two-rows-items',
      itemsPerTile: 4,
    });
    expect(getWelcomeLayout(12, true)).toEqual({
      mode: 'two-rows-compact',
      itemsPerTile: 0,
    });
    expect(getWelcomeLayout(9, true)).toEqual({
      mode: 'one-row',
      itemsPerTile: 0,
    });
    expect(getWelcomeLayout(6, true)).toEqual({
      mode: 'summary',
      itemsPerTile: 0,
    });
    expect(getWelcomeLayout(3, true)).toEqual({
      mode: 'inline',
      itemsPerTile: 0,
    });
  });

  it('keeps the post-message welcome more compact', () => {
    expect(getWelcomeLayout(20, false)).toEqual({
      mode: 'two-rows-compact',
      itemsPerTile: 0,
    });
    expect(getWelcomeLayout(21, false)).toEqual({
      mode: 'two-rows-items',
      itemsPerTile: 1,
    });
  });
});

describe('Welcome preview overflow', () => {
  it('uses the server total count when the preview list is shorter', () => {
    expect(getColumnOverflow(22, 8, 8)).toBe(14);
  });

  it('falls back to preview list length when the server omits total count', () => {
    expect(getColumnOverflow(undefined, 8, 8)).toBe(0);
    expect(getColumnOverflow(undefined, 8, 5)).toBe(3);
  });
});
