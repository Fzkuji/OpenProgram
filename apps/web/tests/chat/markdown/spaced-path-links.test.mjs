import assert from 'node:assert/strict';
import test from 'node:test';
import { Marked } from 'marked';
import { cjkEmphasis } from '../../../lib/markdown/cjk-emphasis.ts';
import { spacedPathLinks } from '../../../lib/markdown/spaced-path-links.ts';
import { localPathHref } from '../../../lib/files/prose-file-paths.ts';

const md = new Marked(cjkEmphasis, spacedPathLinks);
const html = (s) => md.parseInline(s);
const href = (s) => /href="([^"]+)"/.exec(html(s))?.[1];

test('a local path with spaces renders as a link that resolves back to the path', () => {
  const path = '/Users/me/Projects/Conferences/ICDM 2026/Slides/ICDM2026-组会参会分享.pptx';
  const out = html(`[ICDM2026-组会参会分享.pptx](${path}) 是重新保存的标准文件`);
  assert.match(out, /^<a href="[^"]+">ICDM2026-组会参会分享\.pptx<\/a> 是重新保存的标准文件$/);
  assert.equal(localPathHref(href(`[x](${path})`)), path);
});

test('home, drive and file:// paths, and parentheses inside the name', () => {
  assert.equal(localPathHref(href('[a](~/My Files/a.md)')), '~/My Files/a.md');
  assert.equal(localPathHref(href('[b](/tmp/deck (1) copy.pptx).')), '/tmp/deck (1) copy.pptx');
  assert.ok(href('[c](C:\\My Files\\c.txt)'));
  assert.ok(href('[d](file:///Users/me/A B/d.pdf)'));
});

test('everything else keeps stock CommonMark behaviour', () => {
  assert.equal(html('[a](/plain/path.md)'), '<a href="/plain/path.md">a</a>');
  assert.doesNotMatch(html('[a](https://example.com/a b)'), />a<\/a>/, 'a web address is not guessed at');
  assert.equal(html('[a](relative path.md)'), '[a](relative path.md)');
  assert.equal(html('`[a](/x y/z.md)`'), '<code>[a](/x y/z.md)</code>');
});
