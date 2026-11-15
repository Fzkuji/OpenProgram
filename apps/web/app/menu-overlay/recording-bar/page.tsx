"use client";

/**
 * The floating bar of a demonstration recording (apps/desktop/main/windows/recording-bar.js).
 *
 * Shows the elapsed time and step count from the worker, records the
 * microphone when narration is on and posts each transcribed phrase to
 * the recording, and ends the recording with Done or Discard. The result
 * goes back to the window that started it through the desktop bridge.
 * The bar wears the popover material (POPOVER_SURFACE) inside a
 * transparent gutter the window leaves for its shadow.
 */
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Mic, MicOff, Square, Trash2 } from "lucide-react";

import { desktopBridge } from "@/lib/desktop/bridge/bridge-api";
import { useTranslation } from "@/lib/i18n";
import { VoiceRecorder } from "@/lib/voice/recorder";
import { Button } from "@/components/ui/button";
import { POPOVER_SURFACE } from "@/components/ui/menu/menu-styles";
import styles from "./recording-bar.module.css";

const POLL_MS = 500;
const WARN_SECONDS = 30;

function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Milliseconds of 16-bit mono 16 kHz audio in a WAV blob. */
function wavMs(wav: Blob): number {
  return Math.max(0, (wav.size - 44) / 32);
}

function RecordingBar() {
  const { text } = useTranslation();
  const params = useSearchParams();
  const id = params.get("id") || "";
  const maxSeconds = Number(params.get("max")) || 600;
  const deviceId = params.get("device") || undefined;

  const [elapsed, setElapsed] = useState(0);
  const [steps, setSteps] = useState(0);
  const [micOn, setMicOn] = useState(params.get("mic") === "1");
  const [speaking, setSpeaking] = useState(false);
  const [micError, setMicError] = useState("");
  const [ending, setEnding] = useState<"" | "done" | "discard">("");

  // The worker's clock, so narration lines up with the recorded steps.
  const base = useRef<{ elapsed: number; at: number } | null>(null);
  const recorder = useRef<VoiceRecorder | null>(null);
  const pending = useRef(new Set<Promise<void>>());
  const ended = useRef(false);

  const now = useCallback(() => {
    const b = base.current;
    return b ? b.elapsed + (performance.now() - b.at) : 0;
  }, []);

  const finish = useCallback((summary: unknown, error?: string) => {
    if (ended.current) return;
    ended.current = true;
    recorder.current?.cancel();
    recorder.current = null;
    desktopBridge()?.recording?.finish({
      id,
      summary: summary && typeof summary === "object" ? summary as never : null,
      ...(error ? { error } : {}),
    });
  }, [id]);

  const stop = useCallback(async () => {
    if (ended.current || ending) return;
    setEnding("done");
    recorder.current?.stop();
    recorder.current = null;
    // Give the last phrases a moment to be transcribed and filed.
    const deadline = new Promise((resolve) => setTimeout(resolve, 5000));
    await Promise.race([Promise.all([...pending.current]), deadline]);
    try {
      const response = await fetch(`/api/recordings/${id}/stop`, { method: "POST" });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) finish(null, String(body.error || "The recording could not be stopped."));
      else finish(body);
    } catch {
      finish(null, "Could not reach the worker.");
    }
  }, [ending, finish, id]);

  const discard = useCallback(async () => {
    if (ended.current || ending) return;
    setEnding("discard");
    recorder.current?.cancel();
    recorder.current = null;
    await fetch(`/api/recordings/${id}/discard`, { method: "POST" }).catch(() => {});
    finish(null);
  }, [ending, finish, id]);

  // Status from the worker; a recording that ended by itself (the time
  // limit) is finished as if Done had been pressed.
  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    let live = true;
    const poll = async () => {
      try {
        const response = await fetch(`/api/recordings/${id}`, { cache: "no-store" });
        if (!live) return;
        if (response.status === 404) {
          finish(null, "The recording no longer exists.");
          return;
        }
        const status = await response.json();
        if (!live) return;
        base.current = { elapsed: Number(status.elapsed_ms) || 0, at: performance.now() };
        setSteps(Number(status.steps) || 0);
        if (status.state === "finished" && !ended.current) void stop();
      } catch {
        /* the next poll tries again */
      }
    };
    void poll();
    const timer = setInterval(poll, POLL_MS);
    const tick = setInterval(() => setElapsed(now()), 250);
    return () => {
      live = false;
      clearInterval(timer);
      clearInterval(tick);
    };
  }, [finish, id, now, stop]);

  const narrate = useCallback((wav: Blob) => {
    const t = Math.max(0, Math.round(now() - wavMs(wav)));
    const job = (async () => {
      try {
        const response = await fetch("/api/voice/transcribe", {
          method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav,
        });
        const body = await response.json().catch(() => ({}));
        const said = String(body.text || "").trim();
        if (!response.ok || !said) return;
        await fetch(`/api/recordings/${id}/narration`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t, text: said }),
        });
      } catch {
        /* a lost phrase never stops the recording */
      }
    })();
    pending.current.add(job);
    void job.finally(() => pending.current.delete(job));
  }, [id, now]);

  // The microphone follows the switch.
  useEffect(() => {
    if (!micOn || ended.current) return;
    let cancelled = false;
    const next = new VoiceRecorder({
      onLevel: (_level, isSpeaking) => setSpeaking(isSpeaking),
      onPhrase: narrate,
    }, deviceId);
    next.start().then(() => {
      if (cancelled) next.cancel();
      else {
        recorder.current = next;
        setMicError("");
      }
    }).catch((error) => {
      if (cancelled) return;
      setMicOn(false);
      setMicError(error instanceof DOMException && error.name === "NotAllowedError"
        ? text("Microphone access is off", "麦克风权限已关闭")
        : text("Microphone unavailable", "麦克风不可用"));
    });
    return () => {
      cancelled = true;
      // Turning the switch off keeps what was said up to now.
      if (recorder.current === next) {
        next.stop();
        recorder.current = null;
      }
      setSpeaking(false);
    };
  }, [deviceId, micOn, narrate, text]);

  const remaining = maxSeconds - elapsed / 1000;
  const warning = remaining <= WARN_SECONDS;

  return (
    <div className={styles.stage}>
      <div className={`${styles.bar} ${POPOVER_SURFACE}`} data-recording-bar>
        <span className={styles.mark} aria-hidden="true"><span /></span>
        <span className={styles.time} aria-live="off">{clock(elapsed)}</span>
        <span className={styles.label} data-warning={warning || !!micError ? "true" : "false"}>
          {warning
            ? text(`${Math.max(0, Math.ceil(remaining))} s left`, `还剩 ${Math.max(0, Math.ceil(remaining))} 秒`)
            : micError || text(`${steps} ${steps === 1 ? "step" : "steps"}`, `${steps} 步`)}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${styles.quiet} rounded-full [-webkit-app-region:no-drag]`}
          data-on={micOn ? "true" : "false"}
          data-speaking={speaking ? "true" : "false"}
          aria-pressed={micOn}
          aria-label={micOn ? text("Turn narration off", "关闭讲解") : text("Turn narration on", "开启讲解")}
          disabled={!!ending}
          onClick={() => setMicOn((on) => !on)}
        >
          {micOn ? <Mic size={16} /> : <MicOff size={16} />}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${styles.quiet} rounded-full [-webkit-app-region:no-drag]`}
          aria-label={text("Discard recording", "丢弃录制")}
          disabled={!!ending}
          onClick={() => void discard()}
        >
          <Trash2 size={16} />
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`${styles.stop} rounded-full [-webkit-app-region:no-drag]`}
          aria-label={ending === "done" ? text("Finishing…", "正在完成…") : text("Stop", "停止")}
          data-ending={ending === "done" ? "true" : "false"}
          disabled={!!ending}
          onClick={() => void stop()}
        >
          <Square size={12} fill="currentColor" strokeWidth={0} />
        </Button>
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <RecordingBar />
    </Suspense>
  );
}
