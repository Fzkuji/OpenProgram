import assert from 'node:assert/strict';
import test from 'node:test';
import { duplicateNamePrefixes } from '../../lib/projects/duplicate-names.ts';

test('a name used once gets no prefix', () => {
  const prefixes = duplicateNamePrefixes([
    { id: 'a', name: 'ICDM 2026', path: '/Users/me/Projects/Conferences/ICDM 2026' },
    { id: 'b', name: 'NeurIPS 2026', path: '/Users/me/Projects/Conferences/NeurIPS 2026' },
  ]);
  assert.equal(prefixes.size, 0);
});

test('same-named projects get their differing parent folder', () => {
  const prefixes = duplicateNamePrefixes([
    { id: 'a', name: 'NeurIPS 2026', path: '/Users/me/Projects/Conferences/NeurIPS 2026' },
    { id: 'b', name: 'neurips 2026', path: '/Users/me/Dropbox/NeurIPS 2026' },
  ]);
  assert.equal(prefixes.get('a'), 'Conferences');
  assert.equal(prefixes.get('b'), 'Dropbox');
});

test('equal parents reach further up until they differ', () => {
  const prefixes = duplicateNamePrefixes([
    { id: 'a', name: 'paper', path: '/work/a/drafts/paper' },
    { id: 'b', name: 'paper', path: '/work/b/drafts/paper' },
  ]);
  assert.equal(prefixes.get('a'), 'a/drafts');
  assert.equal(prefixes.get('b'), 'b/drafts');
});
