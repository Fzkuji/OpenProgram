/**
 * transcript-view — how much of the agent's work a conversation shows, as
 * Claude Code's Normal / Thinking / Verbose views do:
 *   normal: tool groups folded;
 *   thinking: tool groups open with their thinking steps open;
 *   verbose: tool groups and every step open.
 * One default for conversations, plus a choice per conversation; both are
 * view preferences of this browser, so they live in localStorage.
 */
import { useSyncExternalStore } from "react";

export type TranscriptView = "normal" | "thinking" | "verbose";

export const TRANSCRIPT_VIEWS: readonly TranscriptView[] = ["normal", "thinking", "verbose"];

const KEY = "openprogram.transcript-view";
const EVENT = "openprogram:transcript-view";
const MAX_SESSIONS = 300;

interface Saved {
  default: TranscriptView;
  sessions: Record<string, TranscriptView>;
}

let cached: Saved | null = null;

const isView = (value: unknown): value is TranscriptView => TRANSCRIPT_VIEWS.includes(value as TranscriptView);

function read(): Saved {
  if (cached) return cached;
  let raw: Partial<Saved> = {};
  try {
    const text = typeof localStorage !== "undefined" ? localStorage.getItem(KEY) : null;
    raw = text ? JSON.parse(text) as Partial<Saved> : {};
  } catch { raw = {}; }
  const sessions: Record<string, TranscriptView> = {};
  if (raw.sessions && typeof raw.sessions === "object") {
    for (const [id, view] of Object.entries(raw.sessions)) if (isView(view)) sessions[id] = view;
  }
  cached = { default: isView(raw.default) ? raw.default : "normal", sessions };
  return cached;
}

function write(next: Saved): void {
  const ids = Object.keys(next.sessions);
  // Oldest choices go first once the list is long; insertion order is kept.
  for (const id of ids.slice(0, Math.max(0, ids.length - MAX_SESSIONS))) delete next.sessions[id];
  cached = next;
  try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* the choice still holds for this page */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(EVENT));
}

export function setDefaultTranscriptView(view: TranscriptView): void {
  write({ ...read(), default: view });
}

export function setSessionTranscriptView(sessionId: string, view: TranscriptView): void {
  const sessions = { ...read().sessions };
  delete sessions[sessionId];
  sessions[sessionId] = view;
  write({ ...read(), sessions });
}

function subscribe(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  const onStorage = (event: StorageEvent) => { if (event.key === KEY) { cached = null; onChange(); } };
  window.addEventListener(EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => { window.removeEventListener(EVENT, onChange); window.removeEventListener("storage", onStorage); };
}

export function useDefaultTranscriptView(): TranscriptView {
  return useSyncExternalStore(subscribe, () => read().default, () => "normal");
}

export function useTranscriptView(sessionId: string | null | undefined): TranscriptView {
  return useSyncExternalStore(
    subscribe,
    () => (sessionId && read().sessions[sessionId]) || read().default,
    () => "normal",
  );
}

/** Whether a step starts open in this view. */
export function stepOpenByDefault(view: TranscriptView, kind: string): boolean {
  return view === "verbose" || (view === "thinking" && kind === "thinking");
}
