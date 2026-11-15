import test from 'node:test';
import assert from 'node:assert/strict';
import {
  THUMB_WIDTH,
  captionAlignment,
  thumbCenterX,
} from '../../../lib/effort/effort-matrix.ts';

const WIDTH = 200;
const COUNT = 5;

test('thumb centre follows Radix: half a thumb in from each edge, linear between', () => {
  assert.equal(thumbCenterX(0, COUNT, WIDTH), THUMB_WIDTH / 2);
  assert.equal(thumbCenterX(COUNT - 1, COUNT, WIDTH), WIDTH - THUMB_WIDTH / 2);
  assert.equal(thumbCenterX(2, COUNT, WIDTH), WIDTH / 2);
  assert.equal(thumbCenterX(0, 1, WIDTH), WIDTH / 2, 'a single option sits in the middle');
  assert.equal(thumbCenterX(9, COUNT, WIDTH), WIDTH - THUMB_WIDTH / 2, 'index is clamped');
});

test('the Recommended caption hugs the edges for the end options and centres otherwise', () => {
  assert.equal(captionAlignment(0, COUNT), 'start');
  assert.equal(captionAlignment(COUNT - 1, COUNT), 'end');
  assert.equal(captionAlignment(2, COUNT), 'center');
  assert.equal(captionAlignment(0, 1), 'center');
  assert.equal(captionAlignment(0, 2), 'start');
  assert.equal(captionAlignment(1, 2), 'end');
});
