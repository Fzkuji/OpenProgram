"use client";

/**
 * The composer's suggested next message (Codex composer predictions):
 * when a turn of this conversation ends with the composer empty, ask the
 * worker for one likely follow-up. Tab puts it in the composer as an
 * editable draft; nothing is sent for the user. A new turn, another
 * conversation or a send drops it.
 */
import { useEffect, useRef, useState } from "react";

import { wsRequest } from "@/lib/net/ws/ws-request";
import { useComposerPredictions } from "@/lib/prefs/composer-predictions";

export function useNextPrompt(sessionId: string | null, isRunning: boolean, input: string) {
  const enabled = useComposerPredictions();
  const [suggestion, setSuggestion] = useState<{ sessionId: string; text: string } | null>(null);
  // Whether this same conversation was running at the last render: a switch
  // from a running chat to an idle one is not a turn ending.
  const last = useRef({ sessionId, running: isRunning });
  const inputRef = useRef(input);
  inputRef.current = input;

  useEffect(() => {
    const ended = last.current.sessionId === sessionId && last.current.running && !isRunning;
    last.current = { sessionId, running: isRunning };
    if (isRunning) { setSuggestion(null); return; }
    if (!ended || !enabled || !sessionId || inputRef.current) return;
    let live = true;
    void wsRequest<{ session_id: string; text: string | null }>(
      "predict_next_prompt", { session_id: sessionId }, "next_prompt_prediction", { requestId: true }, 35_000,
    ).then((reply) => {
      if (live && reply?.text && reply.session_id === sessionId) setSuggestion({ sessionId, text: reply.text });
    }).catch(() => {});
    return () => { live = false; };
  }, [isRunning, enabled, sessionId]);

  useEffect(() => { setSuggestion(null); }, [sessionId]);

  const shown = enabled && !isRunning && !input && suggestion?.sessionId === sessionId ? suggestion.text : null;
  return { suggestion: shown, clear: () => setSuggestion(null) };
}
