// Video recording of one pane of this window, asked for by an Agent.
//
// The worker sends `record_panes`, `record_start` and `record_stop` over the
// App socket (the `webtab.command` control plane). This page captures its own
// frame as a media stream, crops the stream to the pane's element, encodes
// H.264 MP4 and hands the chunks to the main process, which writes the file.
// Nothing here is reachable by a person: there is no menu, button or shortcut.
// The only thing a person sees is an outline around the pane being recorded.

import type { CenterTab } from "@/lib/tabs/store/types";
import type { DesktopBridge } from "../bridge/bridge-api";

export type PaneRow = { id: string; kind: string; title: string; width: number; height: number };

export type RecordCommand = {
  op?: string;
  req_id?: string;
  session_id?: string;
  pane?: string;
  path?: string;
  max_duration?: number;
  discard?: boolean;
};

export type RecordReply = {
  ok: boolean;
  error?: string;
  reason_code?: string;
  panes?: PaneRow[];
  recording?: Record<string, string | number | boolean>;
};

type Pane = PaneRow & { element: HTMLElement | null; sessionId?: string };

type Finished = {
  sessionId: string;
  pane: string;
  path: string;
  bytes: number;
  seconds: number;
  width: number;
  height: number;
  fps: number;
  ended: string;
};

type Active = {
  id: string;
  sessionId: string;
  pane: string;
  width: number;
  height: number;
  fps: number;
  /** Ends the recording once; later calls return the same result. */
  end(reason: string, discard?: boolean): Promise<Finished | null>;
};

const MIME = "video/mp4;codecs=avc1";
const FRAME_RATE = 60;
const BITS_PER_SECOND = 8_000_000;
/** How long a window that does not show the conversation waits before it
 *  answers, so the window that does show it answers first. */
const FALLBACK_REPLY_MS = 400;

let active: Active | null = null;
/** A recording that ended by itself, kept until its conversation collects it. */
let finished: Finished | null = null;

const KIND_NAMES: Record<string, string> = {
  session: "conversation", file: "file", web: "web", ntp: "new tab", builtin: "page", application: "application",
};

/** The panes on screen now, by the tab each one shows. */
export function visiblePanes(tabs: readonly CenterTab[], root: ParentNode = document): Pane[] {
  const panes: Pane[] = [];
  const seen = new Set<string>();
  for (const element of root.querySelectorAll<HTMLElement>("[data-center-tab-id], [data-file-pane-id]")) {
    const id = element.dataset.centerTabId || element.dataset.filePaneId || "";
    if (!id || seen.has(id) || element.offsetWidth < 16 || element.offsetHeight < 16) continue;
    const tab = tabs.find(candidate => candidate.id === id);
    if (!tab) continue;
    seen.add(id);
    panes.push({
      id,
      kind: KIND_NAMES[tab.kind] ?? tab.kind,
      title: tab.title || tab.navigationRoute || "",
      width: element.offsetWidth,
      height: element.offsetHeight,
      element,
      sessionId: tab.kind === "session" ? tab.sessionId : undefined,
    });
  }
  return panes;
}

const rows = (panes: readonly Pane[]): PaneRow[] =>
  panes.map(({ id, kind, title, width, height }) => ({ id, kind, title, width, height }));

/** The pane a call names, or why it cannot be recorded. */
export function choosePane(
  name: string | undefined,
  sessionId: string | undefined,
  panes: readonly Pane[],
  windowSize: { width: number; height: number },
): { pane: Pane } | { error: string; reason_code: string } {
  if (name === "window") {
    return { pane: { id: "window", kind: "window", title: "", ...windowSize, element: null } };
  }
  if (name === "conversation") {
    const own = panes.find(pane => pane.kind === "conversation" && !!sessionId && pane.sessionId === sessionId);
    return own ? { pane: own } : {
      reason_code: "conversation_not_shown",
      error: "this conversation's pane is not on screen in a window; panes lists what can be recorded",
    };
  }
  const pane = name ? panes.find(candidate => candidate.id === name) : undefined;
  if (!pane) {
    return {
      reason_code: "unknown_pane",
      error: name ? `no pane ${JSON.stringify(name)} is on screen` : "pane is required: \"conversation\", \"window\", or an id from panes",
    };
  }
  if (pane.kind === "web") {
    return { reason_code: "web_tab_not_recordable", error: "web tabs cannot be recorded yet; record a conversation, file, page or application pane" };
  }
  return { pane };
}

const even = (value: number) => Math.max(2, Math.round(value / 2) * 2);
const hex = () => Array.from(crypto.getRandomValues(new Uint8Array(8)), byte => byte.toString(16).padStart(2, "0")).join("");
const sleep = (ms: number) => new Promise<void>(resolve => window.setTimeout(resolve, ms));

/** An accent line just outside the pane, so it is never in the picture. */
function outline(element: HTMLElement): () => void {
  const ring = document.createElement("div");
  ring.setAttribute("data-pane-recording-outline", "");
  ring.setAttribute("aria-hidden", "true");
  Object.assign(ring.style, {
    position: "fixed", pointerEvents: "none", zIndex: "2147483000", boxSizing: "border-box",
    border: "2px solid var(--theme-accent)", borderRadius: "8px",
  });
  const place = () => {
    const rect = element.getBoundingClientRect();
    Object.assign(ring.style, {
      left: `${rect.left - 2}px`, top: `${rect.top - 2}px`,
      width: `${rect.width + 4}px`, height: `${rect.height + 4}px`,
    });
  };
  place();
  document.body.appendChild(ring);
  const timer = window.setInterval(place, 200);
  return () => { window.clearInterval(timer); ring.remove(); };
}

async function begin(
  bridge: DesktopBridge,
  pane: Pane,
  sessionId: string,
  filePath: string,
  maxSeconds: number,
): Promise<Active> {
  const api = bridge.paneRecording!;
  const scale = window.devicePixelRatio || 1;
  const width = even(pane.width * scale);
  const height = even(pane.height * scale);
  if (!(await api.arm())) throw new Error("the App refused to capture this window");
  // The stream scales what it captures to these sizes, so they are the pane's own pixels.
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: { frameRate: FRAME_RATE, width: { ideal: width }, height: { ideal: height } },
    audio: false,
  });
  const id = hex();
  let removeOutline = () => {};
  let opened = false;
  try {
    const track = stream.getVideoTracks()[0] as MediaStreamTrack & { cropTo?(target: unknown): Promise<void> };
    if (pane.element) {
      const cropTarget = (window as unknown as { CropTarget?: { fromElement(element: Element): Promise<unknown> } }).CropTarget;
      if (!cropTarget || !track.cropTo) throw new Error("this App build cannot crop a capture to a pane");
      await track.cropTo(await cropTarget.fromElement(pane.element));
      // Frames from before the crop are still in flight; let them pass.
      await sleep(250);
      removeOutline = outline(pane.element);
    }
    if (!(await api.open(id, filePath))) throw new Error(`cannot write ${filePath}`);
    opened = true;
    const recorder = new MediaRecorder(stream, { mimeType: MIME, videoBitsPerSecond: BITS_PER_SECOND });
    let writes: Promise<unknown> = Promise.resolve();
    let failed = false;
    recorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      writes = writes.then(async () => {
        if (!(await api.write(id, await event.data.arrayBuffer()))) failed = true;
      }).catch(() => { failed = true; });
    };
    const stopped = new Promise<void>(resolve => { recorder.onstop = () => resolve(); });
    recorder.start(1000);
    const startedAt = performance.now();

    let ending: Promise<Finished | null> | null = null;
    const end = (reason: string, discard = false) => {
      ending ??= (async () => {
        window.clearTimeout(limit);
        window.clearInterval(watch);
        const seconds = Math.round((performance.now() - startedAt) / 10) / 100;
        if (recorder.state !== "inactive") recorder.stop();
        await stopped;
        await writes;
        stream.getTracks().forEach(item => item.stop());
        removeOutline();
        const file = await api.close(id, discard || failed);
        if (active?.id === id) active = null;
        if (discard || failed || !file?.path) return null;
        return { sessionId, pane: pane.id, path: file.path, bytes: file.bytes, seconds, width, height, fps: FRAME_RATE, ended: reason };
      })();
      return ending;
    };
    // A recording nobody stops ends itself, and waits to be collected.
    const keep = (reason: string) => { void end(reason).then(result => { if (result) finished = result; }); };
    const limit = window.setTimeout(() => keep("limit"), maxSeconds * 1000);
    const watch = window.setInterval(() => {
      if (pane.element && (!pane.element.isConnected || pane.element.offsetWidth === 0)) keep("pane closed");
    }, 500);
    track.addEventListener("ended", () => keep("capture ended"));
    return { id, sessionId, pane: pane.id, width, height, fps: FRAME_RATE, end };
  } catch (error) {
    stream.getTracks().forEach(item => item.stop());
    removeOutline();
    if (opened) await api.close(id, true).catch(() => null);
    throw error;
  }
}

const recordingFields = (result: Finished) => ({
  pane: result.pane, path: result.path, bytes: result.bytes, seconds: result.seconds,
  width: result.width, height: result.height, fps: result.fps, ended: result.ended,
});

/** Answer one recording command, or stay silent when another window should. */
export function handleRecordCommand(
  bridge: DesktopBridge,
  command: RecordCommand,
  tabs: readonly CenterTab[],
  reply: (result: RecordReply) => void,
): void {
  const sessionId = command.session_id || "";
  const showsConversation = !!sessionId && tabs.some(tab => tab.kind === "session" && tab.sessionId === sessionId);
  const holds = (active?.sessionId === sessionId || finished?.sessionId === sessionId) && !!sessionId;
  // The window that shows the conversation (or holds its recording) answers at once;
  // the main window answers for a conversation no window shows.
  const delay = showsConversation || holds ? 0 : bridge.windowId === "main" ? FALLBACK_REPLY_MS : -1;
  if (delay < 0) return;
  const run = () => {
    void answer(bridge, command, tabs, sessionId).then(reply, (error: unknown) => reply({
      ok: false, reason_code: "recording_failed", error: error instanceof Error ? error.message : String(error),
    }));
  };
  if (delay) window.setTimeout(run, delay); else run();
}

async function answer(
  bridge: DesktopBridge,
  command: RecordCommand,
  tabs: readonly CenterTab[],
  sessionId: string,
): Promise<RecordReply> {
  const panes = visiblePanes(tabs);
  if (command.op === "record_panes") return { ok: true, panes: rows(panes) };

  if (command.op === "record_stop") {
    if (active && active.sessionId === sessionId) {
      const result = await active.end("stopped", command.discard === true);
      if (command.discard === true) return { ok: true, recording: { discarded: true } };
      if (!result) return { ok: false, reason_code: "recording_failed", error: "the recording could not be written" };
      return { ok: true, recording: recordingFields(result) };
    }
    if (finished && finished.sessionId === sessionId) {
      const result = finished;
      finished = null;
      return { ok: true, recording: recordingFields(result) };
    }
    return { ok: false, reason_code: "not_recording", error: "Nothing is being recorded; call start_video_recording first." };
  }

  if (!bridge.paneRecording || typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported(MIME)) {
    return { ok: false, reason_code: "recording_unavailable", error: "this App build cannot record video; update the desktop App" };
  }
  if (active) {
    return {
      ok: false, reason_code: "already_recording",
      error: active.sessionId === sessionId
        ? `Already recording ${active.pane}; call stop_video_recording first.`
        : "Another conversation is recording in this window; try again when it has stopped.",
    };
  }
  const chosen = choosePane(command.pane, sessionId, panes, { width: window.innerWidth, height: window.innerHeight });
  if ("error" in chosen) return { ok: false, ...chosen, panes: rows(panes) };
  const seconds = Number(command.max_duration);
  if (!command.path || !(seconds > 0)) return { ok: false, reason_code: "recording_failed", error: "the recording request is incomplete" };
  // A result nobody collected belongs to an earlier recording.
  if (finished?.sessionId === sessionId) finished = null;
  active = await begin(bridge, chosen.pane, sessionId, command.path, seconds);
  return {
    ok: true,
    recording: { pane: active.pane, width: active.width, height: active.height, fps: active.fps, max_duration: seconds },
  };
}
