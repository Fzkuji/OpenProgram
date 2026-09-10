"use client";

import { Suspense, useEffect, useState } from "react";

import { CursorClickIcon } from "@/components/animated-icons";

interface CuePayload {
  play: "once" | "never";
  playSeq?: number;
  reducedMotion?: boolean;
}

interface CueBridge {
  ready(): void;
  onCue(cb: (payload: CuePayload) => void): () => void;
}

function cueBridge(): CueBridge | null {
  const api = (
    window as unknown as {
      openprogramDesktop?: { actionCueOverlay?: CueBridge };
    }
  ).openprogramDesktop?.actionCueOverlay;
  return api ?? null;
}

function ActionCuePage() {
  const [cue, setCue] = useState<CuePayload>({ play: "never", playSeq: 0 });

  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    const api = cueBridge();
    if (!api) return;
    const stop = api.onCue((payload) => {
      setCue({
        play: payload.reducedMotion ? "never" : payload.play,
        playSeq: payload.playSeq,
        reducedMotion: payload.reducedMotion,
      });
    });
    api.ready();
    return stop;
  }, []);

  return (
    <div data-browser-action-cue="true" style={{ pointerEvents: "none", color: "#e07a22" }}>
      <CursorClickIcon
        key={String(cue.playSeq ?? 0)}
        size={28}
        play={cue.reducedMotion ? "never" : cue.play}
      />
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <ActionCuePage />
    </Suspense>
  );
}
