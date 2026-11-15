import assert from 'node:assert/strict';
import test from 'node:test';
import { Marked } from 'marked';
import { cjkEmphasis } from '../../../lib/markdown/cjk-emphasis.ts';

const md = new Marked(cjkEmphasis);
const html = (s) => md.parseInline(s);

test('bold closes after CJK punctuation followed by CJK text', () => {
  assert.equal(html('**谁写的：目前不能确认。**能确认的是'), '<strong>谁写的：目前不能确认。</strong>能确认的是');
  assert.equal(html('这是**中文，**加粗'), '这是<strong>中文，</strong>加粗');
  assert.equal(html('说**“引号”**好'), '说<strong>“引号”</strong>好');
});

test('nested inline markdown inside the bold still renders', () => {
  assert.equal(html('**看 `code` 这里。**然后'), '<strong>看 <code>code</code> 这里。</strong>然后');
});

test('ordinary emphasis keeps stock CommonMark behaviour', () => {
  assert.equal(html('**历史顺序**：名单'), '<strong>历史顺序</strong>：名单');
  assert.equal(html('a **b** c'), 'a <strong>b</strong> c');
  assert.equal(html('**bold.**next'), '**bold.**next');
  assert.equal(html('2 ** 3 ** 4'), '2 ** 3 ** 4');
  assert.equal(html('**加粗。** 后面'), '<strong>加粗。</strong> 后面');
});
