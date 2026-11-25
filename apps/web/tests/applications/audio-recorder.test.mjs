import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

// An application's recording: the page records only the microphone and posts it
// to the worker, which records the computer's sound and says whether it could.
const root = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = await mkdtemp(join(root, ".audio-recorder-"));
after(() => rm(dir, { recursive: true, force: true }));
await build({
  absWorkingDir: root,
  stdin: { contents: "export { useAudioRecorder, createChunkClock } from './lib/applications/audio-recorder';", resolveDir: root },
  bundle: true, format: "esm", platform: "node", packages: "external",
  tsconfig: join(root, "tsconfig.json"), outfile: join(dir, "probe.mjs"),
});

const asked = { user: [], display: 0 };
const requests = [];

class FakeTrack {
  readyState = "live";
  stop() { this.readyState = "ended"; }
}

class FakeStream {
  tracks = [new FakeTrack()];
  getAudioTracks() { return this.tracks; }
  getTracks() { return this.tracks; }
}

class FakeMediaRecorder {
  static isTypeSupported(type) { return type === "audio/webm;codecs=opus"; }
  constructor(stream, options) {
    this.mimeType = options?.mimeType ?? "";
    this.state = "inactive";
    this.listeners = [];
  }
  start() { this.state = "recording"; }
  addEventListener(name, listener) { if (name === "stop") this.listeners.push(listener); }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["opus"], { type: this.mimeType }) });
    this.onstop?.();
    for (const listener of this.listeners) listener();
  }
}

Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: {
    mediaDevices: {
      getUserMedia: async (constraints) => { asked.user.push(constraints); return new FakeStream(); },
      getDisplayMedia: async () => { asked.display += 1; throw new Error("getDisplayMedia must not be called"); },
    },
  },
});
globalThis.MediaRecorder = FakeMediaRecorder;

const BASE = "/api/application-instances/i1/audio";
globalThis.fetch = async (url, init = {}) => {
  const sent = typeof init.body === "string" ? JSON.parse(init.body) : null;
  const path = String(url);
  requests.push({ url: path, method: init.method ?? "GET", body: sent });
  const recording = { id: "r1", key: sent?.key ?? "m1", state: path.endsWith("/stop") ? "processing" : "recording",
    elapsed_ms: 1200, system_audio: "unavailable", system_audio_reason: "permission", system_audio_message: "declined" };
  const body = path === BASE && init.method !== "POST" ? { recording } : recording;
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
};

const { useAudioRecorder, createChunkClock } = await import(pathToFileURL(join(dir, "probe.mjs")));
const settle = () => new Promise(resolve => setTimeout(resolve, 150)); // long enough for a chunk to hold sound

test("start asks only for the microphone and keeps the worker's word on the computer's sound", async () => {
  const before = performance.now();
  const started = await useAudioRecorder.getState().start("i1", { key: "m1", tracks: ["mic", "system"], max_seconds: 600 });
  assert.equal(started.ok, true);
  assert.equal(asked.display, 0);
  assert.equal(asked.user.length, 1);
  assert.deepEqual(Object.keys(asked.user[0]), ["audio"]);
  assert.equal(asked.user[0].audio.echoCancellation, true);
  assert.deepEqual(requests[0], { url: BASE, method: "POST", body: { key: "m1", tracks: ["mic", "system"], max_seconds: 600 } });
  assert.equal(started.status.recording.system_audio_reason, "permission");
  const microphone = useAudioRecorder.getState().microphones.i1;
  assert.equal(microphone.phase, "recording");
  assert.ok(microphone.startedAt <= performance.timeOrigin + before - 1200 + 50, "the page counts from the worker's start");

  await settle();
  await useAudioRecorder.getState().stop("i1");
  const uploads = requests.filter(request => request.url.includes("/chunks?"));
  assert.equal(uploads.length, 1);
  assert.ok(uploads[0].url.startsWith(`${BASE}/r1/chunks?`));
  const query = new URL(uploads[0].url, "http://x").searchParams;
  assert.equal(query.get("track"), "mic");
  assert.equal(query.get("seq"), "0");
  assert.ok(Number(query.get("t_ms")) >= 1200, "the first microphone chunk starts where the worker's clock is");
  assert.ok(requests.some(request => request.url === `${BASE}/r1/stop` && request.method === "POST"));
  assert.equal(useAudioRecorder.getState().microphones.i1.phase, "idle");
});

test("a second start while recording is refused without asking for the microphone again", async () => {
  asked.user.length = 0;
  assert.equal((await useAudioRecorder.getState().start("i1", { key: "m1" })).ok, true);
  const again = await useAudioRecorder.getState().start("i1", { key: "m2" });
  assert.equal(again.ok, false);
  assert.equal(asked.user.length, 1);
  await useAudioRecorder.getState().stop("i1");
});

test("a chunk stopped within a few milliseconds of starting is not uploaded", async () => {
  requests.length = 0;
  assert.equal((await useAudioRecorder.getState().start("i1", { key: "blip" })).ok, true);
  await useAudioRecorder.getState().stop("i1");
  assert.equal(requests.filter(request => request.url.includes("/chunks?")).length, 0,
    "a WebM header with no sound in it is not a chunk");
});

test("each chunk carries its number and its offset from the recording's start", () => {
  const chunks = createChunkClock(1000);
  assert.deepEqual(chunks.next(1000), { seq: 0, t_ms: 0 });
  assert.deepEqual(chunks.next(31_000.4), { seq: 1, t_ms: 30_000 });
  assert.deepEqual(chunks.next(500), { seq: 2, t_ms: 0 });
});
