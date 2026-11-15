/**
 * Pure audio helpers for composer voice input: split the microphone
 * stream into phrases at pauses, encode a phrase as 16-bit WAV, and join
 * the transcribed phrases into the input text.
 *
 * Each phrase is transcribed once as soon as the speaker pauses, so text
 * appears sentence by sentence without re-sending audio already done.
 */

export interface SegmenterOptions {
  sampleRate: number;
  /** Pause that ends a phrase. */
  silenceMs?: number;
  /** Shorter bursts (a click, a cough) are dropped, not transcribed. */
  minSpeechMs?: number;
  /** A phrase is cut here even without a pause. */
  maxPhraseMs?: number;
  /** Audio kept from before speech starts, so first syllables survive. */
  preRollMs?: number;
}

export interface FrameResult {
  /** 0..1 loudness for the waveform strip. */
  level: number;
  speaking: boolean;
  /** A finished phrase, when this frame ended one. */
  phrase?: Float32Array;
}

const MIN_THRESHOLD = 0.012;

export function rms(frame: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return frame.length ? Math.sqrt(sum / frame.length) : 0;
}

/** Loudness mapped onto 0..1 for display (speech sits around 0.3–0.9). */
export function displayLevel(value: number): number {
  if (value <= 0) return 0;
  const db = 20 * Math.log10(value);
  return Math.max(0, Math.min(1, (db + 55) / 45));
}

export class PhraseSegmenter {
  private readonly rate: number;
  private readonly silence: number;
  private readonly minSpeech: number;
  private readonly maxPhrase: number;
  private readonly preRoll: number;
  private noise = MIN_THRESHOLD / 3;
  private chunks: Float32Array[] = [];
  private length = 0;
  private speech = 0;
  private quiet = 0;
  private inPhrase = false;
  private preChunks: Float32Array[] = [];
  private preLength = 0;

  constructor(options: SegmenterOptions) {
    this.rate = options.sampleRate;
    this.silence = ((options.silenceMs ?? 700) / 1000) * this.rate;
    this.minSpeech = ((options.minSpeechMs ?? 250) / 1000) * this.rate;
    this.maxPhrase = ((options.maxPhraseMs ?? 25_000) / 1000) * this.rate;
    this.preRoll = ((options.preRollMs ?? 300) / 1000) * this.rate;
  }

  push(frame: Float32Array): FrameResult {
    const value = rms(frame);
    const threshold = Math.max(MIN_THRESHOLD, this.noise * 3);
    const speaking = value > threshold;
    if (!speaking) this.noise = this.noise * 0.95 + value * 0.05;
    const result: FrameResult = { level: displayLevel(value), speaking };

    if (!this.inPhrase) {
      if (speaking) {
        this.inPhrase = true;
        for (const chunk of this.preChunks) this.append(chunk);
        this.preChunks = [];
        this.preLength = 0;
        this.append(frame);
        this.speech = frame.length;
        this.quiet = 0;
      } else {
        this.preChunks.push(frame);
        this.preLength += frame.length;
        while (this.preLength - this.preChunks[0].length >= this.preRoll) {
          this.preLength -= this.preChunks.shift()!.length;
        }
      }
      return result;
    }

    this.append(frame);
    if (speaking) {
      this.speech += frame.length;
      this.quiet = 0;
    } else {
      this.quiet += frame.length;
    }
    if (this.quiet >= this.silence || this.length >= this.maxPhrase) {
      const phrase = this.take();
      if (phrase) result.phrase = phrase;
    }
    return result;
  }

  /** The phrase in progress when recording stops, if it held speech. */
  flush(): Float32Array | null {
    return this.inPhrase ? this.take() : null;
  }

  private append(frame: Float32Array) {
    this.chunks.push(frame);
    this.length += frame.length;
  }

  private take(): Float32Array | null {
    const enough = this.speech >= this.minSpeech;
    const out = enough ? concat(this.chunks, this.length) : null;
    this.chunks = [];
    this.length = 0;
    this.speech = 0;
    this.quiet = 0;
    this.inPhrase = false;
    return out;
  }
}

function concat(chunks: Float32Array[], length: number): Float32Array {
  const out = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}

/** 16-bit mono PCM WAV. */
export function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const ascii = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

const LATIN_END = /[A-Za-z0-9.,!?;:)'"]$/;
const LATIN_START = /^[A-Za-z0-9("']/;

/**
 * Text to insert after `before` for a newly transcribed phrase: a space
 * between two Latin-script words, nothing around CJK text.
 */
export function joinPhrase(before: string, phrase: string): string {
  const text = phrase.trim();
  if (!text) return "";
  return LATIN_END.test(before) && LATIN_START.test(text) ? ` ${text}` : text;
}
