import test from 'node:test';
import assert from 'node:assert/strict';
import { PhraseSegmenter, encodeWav, joinPhrase } from '../../../lib/voice/segmenter.ts';

const RATE = 16000;
const FRAME = 1024;

function tone(amplitude) {
  const frame = new Float32Array(FRAME);
  for (let i = 0; i < FRAME; i++) frame[i] = amplitude * Math.sin((2 * Math.PI * 220 * i) / RATE);
  return frame;
}

function feed(segmenter, frames) {
  const phrases = [];
  for (const frame of frames) {
    const result = segmenter.push(frame);
    if (result.phrase) phrases.push(result.phrase);
  }
  return phrases;
}

const quiet = (n) => Array.from({ length: n }, () => tone(0.001));
const loud = (n) => Array.from({ length: n }, () => tone(0.3));

test('a pause after speech ends one phrase, with pre-roll kept', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE, silenceMs: 700, preRollMs: 300 });
  const phrases = feed(segmenter, [...quiet(20), ...loud(15), ...quiet(12)]);
  assert.equal(phrases.length, 1);
  // 15 loud frames plus some pre-roll and the trailing pause.
  assert.ok(phrases[0].length > 15 * FRAME);
  assert.ok(phrases[0].length < 35 * FRAME);
  assert.equal(segmenter.flush(), null);
});

test('two sentences separated by a pause become two phrases', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE });
  const phrases = feed(segmenter, [...loud(10), ...quiet(12), ...loud(10), ...quiet(12)]);
  assert.equal(phrases.length, 2);
});

test('a click shorter than the minimum is dropped', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE, minSpeechMs: 250 });
  const phrases = feed(segmenter, [...quiet(10), ...loud(1), ...quiet(15)]);
  assert.equal(phrases.length, 0);
});

test('flush returns the phrase still being spoken', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE });
  feed(segmenter, loud(10));
  const rest = segmenter.flush();
  assert.ok(rest && rest.length >= 10 * FRAME);
});

test('a phrase is cut at the maximum length without a pause', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE, maxPhraseMs: 1000 });
  const phrases = feed(segmenter, loud(40));
  assert.ok(phrases.length >= 2);
});

test('levels are 0..1 and louder input reads higher', () => {
  const segmenter = new PhraseSegmenter({ sampleRate: RATE });
  const soft = segmenter.push(tone(0.02)).level;
  const strong = segmenter.push(tone(0.5)).level;
  assert.ok(soft >= 0 && strong <= 1);
  assert.ok(strong > soft);
});

test('encodeWav writes a 16-bit mono PCM header', () => {
  const wav = new DataView(encodeWav(new Float32Array([0, 1, -1]), RATE));
  const tag = (offset) => String.fromCharCode(...[0, 1, 2, 3].map((i) => wav.getUint8(offset + i)));
  assert.equal(tag(0), 'RIFF');
  assert.equal(tag(8), 'WAVE');
  assert.equal(wav.getUint16(22, true), 1);
  assert.equal(wav.getUint32(24, true), RATE);
  assert.equal(wav.getUint16(34, true), 16);
  assert.equal(wav.getUint32(40, true), 6);
  assert.equal(wav.getInt16(46, true), 0x7fff);
  assert.equal(wav.getInt16(48, true), -0x8000);
});

test('joinPhrase spaces Latin words and never CJK text', () => {
  assert.equal(joinPhrase('Hello', 'world.'), ' world.');
  assert.equal(joinPhrase('Hello ', 'world'), 'world');
  assert.equal(joinPhrase('', 'Hello'), 'Hello');
  assert.equal(joinPhrase('你好。', '再见'), '再见');
  assert.equal(joinPhrase('ok', '好的'), '好的');
  assert.equal(joinPhrase('ok', '   '), '');
});
