"use client";

/**
 * The composer's suggested next message (Codex composer predictions).
 * Whenever an idle conversation is on screen with the composer empty — its
 * turn just ended here, or the user came back to it after it finished in
 * the background — ask the worker for one likely follow-up. The worker
 * keeps each prediction against the conversation's last message, so coming
 * back to a chat costs no new model call. Tab puts it in the composer as an
 * editable draft; nothing is sent for the user. A new turn or a send drops it.
 */
import { useEffect, useRef, useState } from "react";

import { wsRequest } from "@/lib/net/ws/ws-request";
import { useComposerPredictions } from "@/lib/prefs/composer-predictions";

export function useNextPrompt(sessionId: string | null, isRunning: boolean, input: string) {
  const enabled = useComposerPredictions();
  const [suggestion, setSuggestion] = useState<{ sessionId: string; text: string } | null>(null);
  // Whether this same conversation was running at the last render: a switch
  // from a running chat to an idle one is an opening, not a turn ending.
  const last = useRef({ sessionId, running: isRunning });
  const inputRef = useRef(input);
  inputRef.current = input;

  useEffect(() => {
    const ended = last.current.sessionId === sessionId && last.current.running && !isRunning;
    last.current = { sessionId, running: isRunning };
    setSuggestion(null);
    if (isRunning || !enabled || !sessionId || inputRef.current) return;
    let live = true;
    void wsRequest<{ session_id: string; text: string | null }>(
      "predict_next_prompt", { session_id: sessionId, reason: ended ? "turn_end" : "opened" },
      "next_prompt_prediction", { requestId: true }, 35_000,
    ).then((reply) => {
      if (live && reply?.text && reply.session_id === sessionId) setSuggestion({ sessionId, text: reply.text });
    }).catch(() => {});
    return () => { live = false; };
  }, [isRunning, enabled, sessionId]);

  const shown = enabled && !isRunning && !input && suggestion?.sessionId === sessionId ? suggestion.text : null;
  return { suggestion: shown, clear: () => setSuggestion(null) };
}
