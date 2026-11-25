import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

import { applicationRows, BUILTIN_TABS } from "../../components/applications/application-rows.ts";

// The Meetings window's sources live in its application package and import the
// web app's modules by its "@/" alias, so they are bundled before they are read.
const root = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const source = join(root, "../../openprogram/programs/applications/meeting/web/src/meeting-format.ts");
const dir = await mkdtemp(join(root, ".meeting-format-"));
after(() => rm(dir, { recursive: true, force: true }));
await build({
  absWorkingDir: root, entryPoints: [source], bundle: true, format: "esm", platform: "node",
  packages: "external", tsconfig: join(root, "tsconfig.json"), outfile: join(dir, "probe.mjs"),
  plugins: [{ name: "alias", setup(b) {
    b.onResolve({ filter: /^@\// }, args => b.resolve("./" + args.path.slice(2), { resolveDir: root, kind: args.kind }));
  } }],
});
const {
  captureErrorMessage, chunkProgress, clock, languageLabel, recordingNote, shownState, speakerLabel, stateLabel,
  systemAudioOf, transcriptText,
} = await import(pathToFileURL(join(dir, "probe.mjs")));

const en = (english) => english;
const zh = (_english, chinese) => chinese;

test("clock shows minutes and seconds, hours from an hour on", () => {
  assert.equal(clock(0), "00:00");
  assert.equal(clock(65_400), "01:05");
  assert.equal(clock(3_725_000), "1:02:05");
  assert.equal(clock(-5), "00:00");
});

test("speakers are named in the reader's language, numbered when told apart", () => {
  assert.equal(speakerLabel("me", en), "Me");
  assert.equal(speakerLabel("others", zh), "对方");
  assert.equal(speakerLabel("others:2", en), "Speaker 2");
  assert.equal(speakerLabel("others:1", zh), "发言人 1");
});

test("the transcript reads one timed line per turn", () => {
  const lines = [
    { start_ms: 500, end_ms: 2000, speaker: "me", text: "Shall we start?" },
    { start_ms: 62_000, end_ms: 64_000, speaker: "others", text: "Yes." },
  ];
  assert.equal(transcriptText(lines, en), "[00:00] Me: Shall we start?\n[01:02] Others: Yes.");
});

test("the meeting's language reads by its own name, or as detected when none was chosen", () => {
  assert.equal(languageLabel("en", en), "Language: English");
  assert.equal(languageLabel("zh", zh), "语言：中文");
  assert.equal(languageLabel("", en), "Language: detected");
  assert.equal(languageLabel(undefined, zh), "语言：自动识别");
  assert.equal(languageLabel("pt", en), "Language: pt");
});

test("chunk progress counts both tracks and what still waits", () => {
  assert.deepEqual(chunkProgress({ chunks: { mic: 3, system: 2 }, transcribed: 2, skipped: 1, failed: 1 }),
    { received: 5, processed: 4, pending: 1 });
  assert.deepEqual(chunkProgress({ chunks: {}, transcribed: 0, skipped: 0, failed: 0 }),
    { received: 0, processed: 0, pending: 0 });
});

test("a refused microphone names the System Settings pane", () => {
  assert.match(captureErrorMessage("NotAllowedError", en), /Privacy & Security → Microphone/);
  assert.match(captureErrorMessage("NotFoundError", zh), /麦克风/);
  assert.equal(stateLabel("summarizing", en), "Summarizing");
});

test("the recording note says whether the server records the computer's sound, and why not", () => {
  const recording = systemAudioOf({ system_audio: "recording", system_audio_reason: "", system_audio_message: "" });
  assert.equal(recordingNote(recording, en), "Recording your microphone and the computer's sound.");
  const refused = systemAudioOf({ system_audio: "unavailable", system_audio_reason: "permission" });
  assert.match(recordingNote(refused, en), /^Recording your microphone only/);
  assert.match(recordingNote(refused, en), /Privacy & Security → Screen & System Audio Recording, then reopen the App/);
  assert.match(recordingNote(refused, zh), /录屏与系统录音/);
  assert.match(recordingNote(systemAudioOf({ system_audio: "unavailable", system_audio_reason: "unsupported" }), en), /macOS only/);
  assert.match(recordingNote(systemAudioOf({ system_audio: "unavailable", system_audio_reason: "error", system_audio_message: "stream broke" }), en), /\(stream broke\)$/);
  assert.deepEqual(systemAudioOf({}), { state: "", reason: "", message: "" });
});

test("a meeting the document calls live that nothing works on any more reads as interrupted", () => {
  const live = { id: "m1", state: "recording" };
  assert.equal(shownState(live, { key: "m1", state: "recording" }, false), "recording");
  assert.equal(shownState(live, { key: "m1", state: "processing" }, false), "transcribing");
  assert.equal(shownState(live, { key: "m2", state: "recording" }, false), "interrupted");
  assert.equal(shownState(live, null, false), "interrupted");
  assert.equal(shownState({ id: "m1", state: "summarizing" }, null, true), "summarizing");
  assert.equal(shownState({ id: "m1", state: "summarizing" }, null, false), "interrupted");
  assert.equal(shownState({ id: "m1", state: "done" }, null, false), "done");
});

test("Meetings opens as an application window, not one of the App's own tabs", () => {
  assert.equal(BUILTIN_TABS.meeting, undefined);
  const [row] = applicationRows([], [{ id: "meeting", title: "Meetings", description: "", icon: "meeting", enabled: true, loaded: true, entries: [] }]);
  assert.equal(row.builtinTab, undefined);
  assert.equal(row.title, "Meetings");
});
