"use client";

/**
 * Composer voice input: the microphone button's state machine.
 *
 *   idle ──click / hold ⌥Space──▶ recording ──click / release──▶ finishing ──▶ idle
 *                                     └──────── Esc ────────▶ idle (text removed)
 *
 * Every phrase the recorder cuts at a pause is posted to
 * /api/voice/transcribe right away; results are inserted in recording
 * order at the caret where recording began, so text appears sentence by
 * sentence while the user keeps talking. Nothing is sent to the chat:
 * the text stays in the input box to edit and send. A recording belongs to
 * the chat it started in: switching chats ends it, and phrases still being
 * transcribed are dropped rather than typed into the other chat.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { VoiceRecorder } from "@/lib/voice/recorder";
import { joinPhrase } from "@/lib/voice/segmenter";

export type VoiceState = "idle" | "starting" | "recording" | "finishing";

export interface VoiceLevel {
  level: number;
  speaking: boolean;
}

/** Columns in the waveform strip; the newest sample enters on the right. */
export const VOICE_STRIP_SAMPLES = 22;

const SILENT: VoiceLevel = { level: 0, speaking: false };

interface Options {
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  input: string;
  setInput: (value: string) => void;
  /** The input box; ⌥Space only listens while focus is inside it. */
  wrapperRef: React.RefObject<HTMLElement | null>;
  disabled?: boolean;
  /** The chat whose draft receives the text. */
  owner?: string | null;
}

export function useVoiceInput({ textareaRef, input, setInput, wrapperRef, disabled, owner }: Options) {
  const { text } = useTranslation();
  const [state, setState] = useState<VoiceState>("idle");
  const [levels, setLevels] = useState<VoiceLevel[]>(() => Array(VOICE_STRIP_SAMPLES).fill(SILENT));
  const recorder = useRef<VoiceRecorder | null>(null);
  const inputRef = useRef(input);
  inputRef.current = input;
  const stateRef = useRef(state);
  stateRef.current = state;
  const ownerRef = useRef(owner);

  // Insertion bookkeeping for the current recording.
  const anchor = useRef(0);
  const start0 = useRef(0);
  const inserted = useRef("");
  const nextSeq = useRef(0);
  const nextInsert = useRef(0);
  const results = useRef(new Map<number, string>());
  const pending = useRef(0);
  const generation = useRef(0);
  // Bumped by cancel, a chat switch and unmount: a start() still waiting
  // on the status check or the microphone sees it changed and backs out.
  const attempt = useRef(0);
  // Hold-to-talk released before the microphone opened.
  const stopWhenStarted = useRef(false);
  // ⌥Space is held down; survives the key listeners being re-attached.
  const holding = useRef(false);

  const finishIfDone = useCallback(() => {
    if (stateRef.current === "finishing" && pending.current === 0) setState("idle");
  }, []);

  const flushResults = useCallback(() => {
    let value = inputRef.current;
    let changed = false;
    while (results.current.has(nextInsert.current)) {
      const phrase = results.current.get(nextInsert.current)!;
      results.current.delete(nextInsert.current);
      nextInsert.current += 1;
      const at = Math.min(anchor.current, value.length);
      const piece = joinPhrase(value.slice(0, at), phrase);
      if (!piece) continue;
      value = value.slice(0, at) + piece + value.slice(at);
      anchor.current = at + piece.length;
      inserted.current += piece;
      changed = true;
    }
    if (changed) {
      inputRef.current = value;
      setInput(value);
      const caret = anchor.current;
      requestAnimationFrame(() => textareaRef.current?.setSelectionRange(caret, caret));
    }
  }, [setInput, textareaRef]);

  const transcribe = useCallback(async (wav: Blob) => {
    const seq = nextSeq.current++;
    const gen = generation.current;
    pending.current += 1;
    try {
      const response = await fetch("/api/voice/transcribe", {
        method: "POST",
        headers: { "Content-Type": "audio/wav" },
        body: wav,
      });
      const body = await response.json().catch(() => ({}));
      if (gen !== generation.current) return;
      if (!response.ok || body.error) {
        results.current.set(seq, "");
        showToast(body.error || text("Transcription failed.", "转写失败。"), { tone: "error" });
      } else {
        results.current.set(seq, String(body.text || ""));
      }
      flushResults();
    } catch {
      if (gen !== generation.current) return;
      results.current.set(seq, "");
      flushResults();
      showToast(text("Could not reach the worker to transcribe.", "无法连接后台进行转写。"), { tone: "error" });
    } finally {
      if (gen === generation.current) {
        pending.current -= 1;
        finishIfDone();
      }
    }
  }, [finishIfDone, flushResults, text]);

  const stop = useCallback(() => {
    if (stateRef.current !== "recording") return;
    recorder.current?.stop();
    recorder.current = null;
    stateRef.current = "finishing";
    setState("finishing");
    finishIfDone();
  }, [finishIfDone]);

  const start = useCallback(async () => {
    if (stateRef.current !== "idle" || disabled) return;
    const startedFor = ownerRef.current;
    const mine = ++attempt.current;
    stopWhenStarted.current = false;
    stateRef.current = "starting";
    setState("starting");
    try {
      const status = await fetch("/api/voice/status").then((r) => r.json());
      if (attempt.current !== mine) return;
      if (!status?.resolved_engine) {
        showToast(text(
          "No speech engine is set up. Choose one in Settings → Voice.",
          "还没有可用的语音识别服务，请在 设置 → 语音 里选择。",
        ), { tone: "error" });
        setState("idle");
        return;
      }
    } catch {
      // The transcribe call reports a missing worker on its own.
    }
    if (attempt.current !== mine) return;
    generation.current += 1;
    const caret = textareaRef.current?.selectionStart ?? inputRef.current.length;
    anchor.current = caret;
    start0.current = caret;
    inserted.current = "";
    nextSeq.current = 0;
    nextInsert.current = 0;
    results.current.clear();
    pending.current = 0;
    setLevels(Array(VOICE_STRIP_SAMPLES).fill(SILENT));
    const next = new VoiceRecorder({
      onLevel: (level, speaking) =>
        setLevels((prev) => [...prev.slice(1), { level, speaking }]),
      onPhrase: (wav) => void transcribe(wav),
    });
    try {
      await next.start();
    } catch (error) {
      // A half-opened recorder may hold the stream: always release it.
      next.cancel();
      if (attempt.current !== mine) return;
      stateRef.current = "idle";
      setState("idle");
      const denied = error instanceof DOMException && error.name === "NotAllowedError";
      showToast(denied
        ? text("Microphone access is off. Allow OpenProgram in System Settings → Privacy → Microphone.",
          "麦克风权限被关闭。请在 系统设置 → 隐私与安全性 → 麦克风 里允许 OpenProgram。")
        : text(`Could not open the microphone: ${String(error)}`, `无法打开麦克风：${String(error)}`),
      { tone: "error" });
      return;
    }
    if (attempt.current !== mine) {
      // Cancelled or unmounted while the microphone opened.
      next.cancel();
      return;
    }
    if (ownerRef.current !== startedFor) {
      // The chat changed while the microphone opened.
      next.cancel();
      setState("idle");
      return;
    }
    recorder.current = next;
    stateRef.current = "recording";
    setState("recording");
    if (stopWhenStarted.current) {
      stopWhenStarted.current = false;
      stop();
    }
  }, [disabled, stop, textareaRef, text, transcribe]);

  const cancel = useCallback(() => {
    if (stateRef.current === "idle") return;
    attempt.current += 1;
    stopWhenStarted.current = false;
    recorder.current?.cancel();
    recorder.current = null;
    generation.current += 1;
    pending.current = 0;
    results.current.clear();
    // Take back what this recording typed, if it is still where we put it.
    const value = inputRef.current;
    const from = start0.current;
    const typed = inserted.current;
    if (typed && value.slice(from, from + typed.length) === typed) {
      const next = value.slice(0, from) + value.slice(from + typed.length);
      inputRef.current = next;
      setInput(next);
    }
    inserted.current = "";
    stateRef.current = "idle";
    setState("idle");
  }, [setInput]);

  const toggle = useCallback(() => {
    if (stateRef.current === "recording") stop();
    else if (stateRef.current === "idle") void start();
  }, [start, stop]);

  // Esc discards the recording; hold ⌥Space to talk.
  useEffect(() => {
    const inBox = () => {
      const box = wrapperRef.current;
      return !!box && !!document.activeElement && box.contains(document.activeElement);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && stateRef.current !== "idle") {
        event.preventDefault();
        event.stopPropagation();
        cancel();
        return;
      }
      if (event.code === "Space" && event.altKey && !event.metaKey && !event.ctrlKey && inBox()) {
        event.preventDefault();
        if (!event.repeat && stateRef.current === "idle") void start();
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (!(event.code === "Space" || event.key === "Alt") || !holding.current) return;
      if (stateRef.current === "recording") {
        holding.current = false;
        stop();
      } else if (stateRef.current === "starting") {
        // Released before the microphone opened: stop as soon as it does.
        holding.current = false;
        stopWhenStarted.current = true;
      }
    };
    const markHold = (event: KeyboardEvent) => {
      if (event.code === "Space" && event.altKey && inBox()) holding.current = true;
    };
    window.addEventListener("keydown", markHold, true);
    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      window.removeEventListener("keydown", markHold, true);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [cancel, start, stop, wrapperRef]);

  // A chat switch ends the recording. The text already typed stays in the
  // chat it was typed into; late phrases are dropped.
  useEffect(() => {
    if (ownerRef.current === owner) return;
    ownerRef.current = owner;
    if (stateRef.current === "idle") return;
    attempt.current += 1;
    stopWhenStarted.current = false;
    recorder.current?.cancel();
    recorder.current = null;
    generation.current += 1;
    pending.current = 0;
    results.current.clear();
    inserted.current = "";
    stateRef.current = "idle";
    setState("idle");
  }, [owner]);

  // Never leave the microphone open behind an unmounted composer.
  useEffect(() => () => {
    attempt.current += 1;
    recorder.current?.cancel();
    recorder.current = null;
  }, []);

  return { state, levels, start, stop, cancel, toggle };
}
