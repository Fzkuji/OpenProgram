import test from 'node:test';
import assert from 'node:assert/strict';
import { formatEffortLabel } from '../../lib/effort-color.ts';

test('xhigh is a compound token, not Xhigh', () => {
  assert.equal(formatEffortLabel('xhigh'), 'XHigh');
  assert.equal(formatEffortLabel('Xhigh'), 'XHigh');
  assert.equal(formatEffortLabel('high'), 'High');
  assert.equal(formatEffortLabel('max'), 'Max');
  assert.equal(formatEffortLabel(''), '');
});
