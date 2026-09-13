import test from 'node:test';
import assert from 'node:assert/strict';
import { formatUsageFooterLabel } from '../../lib/format-utils/format.ts';

test('mixed Fast responses retain per-call service evidence in the footer', () => {
  const html = formatUsageFooterLabel({input_tokens:10,output_tokens:5,service_tiers:['priority','default','unreported','priority']});
  assert.match(html, /Fast served: 2/);
  assert.match(html, /Standard served: 1/);
  assert.match(html, /Speed unconfirmed: 1/);
});
