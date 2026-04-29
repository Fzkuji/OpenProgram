import { describe, expect, it } from 'vitest';
import { computeScrollbarCells } from '../src/components/TranscriptViewport.js';

describe('TranscriptViewport scrollbar', () => {
  it('renders an empty gutter when content fits', () => {
    expect(computeScrollbarCells(5, 5, 0)).toEqual([
      'empty',
      'empty',
      'empty',
      'empty',
      'empty',
    ]);
  });

  it('places the thumb at the top, middle, and bottom', () => {
    expect(computeScrollbarCells(4, 16, 0)).toEqual([
      'thumb',
      'track',
      'track',
      'track',
    ]);
    expect(computeScrollbarCells(4, 16, 6)).toEqual([
      'track',
      'track',
      'thumb',
      'track',
    ]);
    expect(computeScrollbarCells(4, 16, 12)).toEqual([
      'track',
      'track',
      'track',
      'thumb',
    ]);
  });
});
