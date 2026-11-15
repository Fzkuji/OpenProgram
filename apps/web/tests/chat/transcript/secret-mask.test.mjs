import assert from 'node:assert/strict';
import test from 'node:test';
import {
  collapseSecrets, findSecrets, maskSecret, maskSecrets, secretSentinel,
} from '../../../lib/chat/transcript/secret-mask.ts';

const pat = 'github_pat_' + 'A1b2C3d4E5'.repeat(8);
const classic = 'ghp_' + 'x'.repeat(36);

test('a GitHub token keeps its first characters and hides the rest', () => {
  const masked = maskSecret(pat);
  assert.ok(masked.startsWith('github_p'));
  assert.ok(!masked.includes(pat.slice(8, 20)));
  assert.match(masked.slice(8), /^•+$/);
});

test('secrets are found by prefix and nothing else is', () => {
  const text = `push with ${pat} please, or ${classic}. sk-learn and ghp_short stay.`;
  const found = findSecrets(text).map(({ start, end }) => text.slice(start, end));
  assert.deepEqual(found, [pat, classic]);
});

test('a token glued to other word characters is not a match', () => {
  assert.deepEqual(findSecrets(`x${classic}`), []);
});

test('a private key block is masked as one secret', () => {
  const key = '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\ndef\n-----END OPENSSH PRIVATE KEY-----';
  const masked = maskSecrets(`key:\n${key}\nend`);
  assert.ok(masked.includes('-----BEGIN OPENSSH PRIVATE KEY----- •'));
  assert.ok(!masked.includes('abc'));
});

test('collapse swaps secrets for sentinels in order', () => {
  const { content, secrets } = collapseSecrets(`${classic} then ${pat}`);
  assert.deepEqual(secrets, [classic, pat]);
  assert.equal(content, `${secretSentinel(0)} then ${secretSentinel(1)}`);
});
