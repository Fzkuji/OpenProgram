import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

// The page records only the microphone; the server records the computer's sound
// and says in the start response whether it could.
const root = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const dir = await mkdtemp(join(root, ".meeting-recorder-"));
after(() => rm(dir, { recursive: true, force: true }));
await build({
  absWorkingDir: root,
  stdin: { contents: "export { useMeetingRecorder } from './lib/meetings/meeting-recorder';", resolveDir: root },
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
globalThis.fetch = async (url, init = {}) => {
  const sent = typeof init.body === "string" ? JSON.parse(init.body) : null;
  requests.push({ url: String(url), method: init.method ?? "GET", body: sent });
  const body = String(url) === "/api/meetings"
    ? { id: "abc123", title: "Sync", state: "recording", elapsed_ms: 1200, language: sent?.language ?? "",
      system_audio: "unavailable", system_audio_reason: "permission", system_audio_message: "declined" }
    : { id: "abc123", title: "Sync", state: "transcribing" };
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
};

const { useMeetingRecorder } = await import(pathToFileURL(join(dir, "probe.mjs")));

test("start asks only for the microphone and keeps the server's word on the computer's sound", async () => {
  const before = performance.now();
  assert.equal(await useMeetingRecorder.getState().start("Sync"), true);
  const state = useMeetingRecorder.getState();
  assert.equal(asked.display, 0);
  assert.equal(asked.user.length, 1);
  assert.deepEqual(Object.keys(asked.user[0]), ["audio"]);
  assert.equal(state.phase, "recording");
  assert.deepEqual(requests[0].body, { title: "Sync" }, "no language chosen sends none, so the server detects it");
  assert.equal(state.active.language, "");
  assert.deepEqual(state.active.systemAudio, { state: "unavailable", reason: "permission", message: "declined" });
  assert.ok(state.active.startedAt <= before - 1200 + 50, "the page's clock counts from the server's meeting start");

  await new Promise(resolve => setTimeout(resolve, 150)); // long enough for the chunk to hold sound
  const status = await useMeetingRecorder.getState().stop();
  assert.equal(status.state, "transcribing");
  const uploads = requests.filter(request => request.url.includes("/audio?"));
  assert.equal(uploads.length, 1);
  const query = new URL(uploads[0].url, "http://x").searchParams;
  assert.equal(query.get("track"), "mic");
  assert.equal(query.get("seq"), "0");
  assert.ok(Number(query.get("t_ms")) >= 1200, "the first microphone chunk starts where the server's clock is");
  assert.deepEqual(requests.map(request => request.url).filter(url => !url.includes("/audio?")),
    ["/api/meetings", "/api/meetings/abc123/stop"]);
  assert.equal(useMeetingRecorder.getState().phase, "idle");
});

test("a chosen language is sent on start and kept from the server's answer", async () => {
  requests.length = 0;
  assert.equal(await useMeetingRecorder.getState().start("Standup", "ja"), true);
  assert.deepEqual(requests[0].body, { title: "Standup", language: "ja" });
  assert.equal(useMeetingRecorder.getState().active.language, "ja");
  await useMeetingRecorder.getState().stop();
  assert.equal(useMeetingRecorder.getState().phase, "idle");
});

test("a chunk stopped within a few milliseconds of starting is not uploaded", async () => {
  requests.length = 0;
  assert.equal(await useMeetingRecorder.getState().start("Blip"), true);
  await useMeetingRecorder.getState().stop();
  assert.deepEqual(requests.map(request => request.url), ["/api/meetings", "/api/meetings/abc123/stop"],
    "a WebM header with no sound in it is not a chunk");
});
