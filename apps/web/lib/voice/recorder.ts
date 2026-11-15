/**
 * Microphone capture for composer voice input. Opens the mic only while
 * recording, resamples to 16 kHz in the AudioContext, and hands every
 * finished phrase (cut at a pause by PhraseSegmenter) to `onPhrase` as a
 * WAV blob. `onLevel` feeds the waveform strip about 20 times a second.
 */
import { PhraseSegmenter, encodeWav } from "./segmenter";

const SAMPLE_RATE = 16_000;
const FRAME = 1024; // 64 ms at 16 kHz

export interface RecorderCallbacks {
  onLevel(level: number, speaking: boolean): void;
  onPhrase(wav: Blob): void;
}

export class VoiceRecorder {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private segmenter: PhraseSegmenter | null = null;

  /** `deviceId` picks an input other than the system default. */
  constructor(private readonly callbacks: RecorderCallbacks, private readonly deviceId?: string) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true,
        ...(this.deviceId ? { deviceId: { exact: this.deviceId } } : {}),
      },
    });
    this.context = new AudioContext({ sampleRate: SAMPLE_RATE });
    this.segmenter = new PhraseSegmenter({ sampleRate: this.context.sampleRate });
    this.source = this.context.createMediaStreamSource(this.stream);
    // ScriptProcessor rather than an AudioWorklet: a worklet needs a
    // separately served module, and one 64 ms frame callback is cheap.
    this.processor = this.context.createScriptProcessor(FRAME, 1, 1);
    this.processor.onaudioprocess = (event) => {
      const frame = new Float32Array(event.inputBuffer.getChannelData(0));
      const result = this.segmenter!.push(frame);
      this.callbacks.onLevel(result.level, result.speaking);
      if (result.phrase) this.emit(result.phrase);
    };
    this.source.connect(this.processor);
    this.processor.connect(this.context.destination);
  }

  /** Stop and transcribe what was said since the last pause. */
  stop(): void {
    const rest = this.segmenter?.flush();
    if (rest) this.emit(rest);
    this.release();
  }

  /** Stop and drop the phrase in progress. */
  cancel(): void {
    this.release();
  }

  private emit(samples: Float32Array) {
    const rate = this.context?.sampleRate ?? SAMPLE_RATE;
    this.callbacks.onPhrase(new Blob([encodeWav(samples, rate)], { type: "audio/wav" }));
  }

  private release() {
    if (this.processor) this.processor.onaudioprocess = null;
    this.source?.disconnect();
    this.processor?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.context?.close().catch(() => {});
    this.stream = null;
    this.context = null;
    this.processor = null;
    this.source = null;
    this.segmenter = null;
  }
}
