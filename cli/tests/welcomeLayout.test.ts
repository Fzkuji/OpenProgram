import { describe, expect, it } from 'vitest';
import { getWelcomeLayout } from '../src/components/Welcome.js';

describe('Welcome layout height policy', () => {
  it('keeps the opening panel visible as terminal height decreases', () => {
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
