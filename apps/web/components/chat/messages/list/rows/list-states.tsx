"use client";

/** The list's non-message states: the pending-reply indicator, the
 *  loading skeleton. */
import { ThinkingStatus } from "../../execution/thinking-status";
import { useState } from "react";
import { MessageTimestamp } from "../../bubbles/message-actions";

/** Pin `#chatArea` to the bottom as `#chatMessages` grows, unless the
 *  user has scrolled up. Observes the container rather than threading a
 *  dependency through, so both new bubbles and streamed text deltas
 *  keep the viewport at the bottom.
 *
 *  Also runs KaTeX over freshly-streamed bubbles. ``renderMd`` only
 *  parses markdown-it; it leaves ``\[ ... \]`` / ``$$...$$`` deltas
 *  raw, marked with a ``.md-rendered`` span. The legacy
 *  ``renderMathInChat`` (called by ``scrollToBottom`` in the legacy
 *  path) is what actually swaps math delimiters for KaTeX HTML. The
 *  React message-list never calls ``scrollToBottom`` so streaming
 *  bubbles stayed unrendered until something else (the next send,
 *  page refresh, ...) triggered the legacy hook. Fire it on every
 *  container resize so React-side updates show math live.
 *
 *  ``newTurnSeed`` (the final message ID; unchanged by older pages) marks a new turn.
 *  Following it is conditional: a reader parked at the bottom is carried
 *  along, a reader who scrolled up to re-read something keeps their
 *  place. Their own send always follows — that is an explicit gesture,
 *  not something arriving at them.
 */
/** Breathing "<Agent> is thinking…" indicator shown between a user
 *  msg and the (yet-to-arrive) assistant reply (or an assistant
 *  bubble that exists but is still empty).
 *
 *  Once the bubble has ANY content (text, thinking, tool, runtime
 *  child), that bubble's own streaming UI takes over and this is
 *  hidden by ``MessageList``.
 */
export function PendingReplyIndicator({ timestamp }: { timestamp?: number }) {
  const [fallbackTimestamp] = useState(() => Date.now());
  return (
    <div className="message assistant pending-standalone">
      <div
        className="pending-body"
        /* Short, discrete turn status ("thinking…") — the one thing in
           the transcript worth announcing. Streaming token deltas are
           deliberately left silent (see assistant-bubble). */
        role="status"
        aria-live="polite"
      >
        <ThinkingStatus />
        <MessageTimestamp timestamp={timestamp ?? fallbackTimestamp} />
      </div>
    </div>
  );
}

/** The running operation that is not a reply, named in the indicator's
 *  place: "Compacting context…" while /compact runs or a turn compacts.
 *  It comes from the branch's running task, so a reload shows it again;
 *  the composer's Stop stops it. */
export function OperationStatusIndicator({ label, timestamp }: { label: string; timestamp?: number }) {
  const [fallbackTimestamp] = useState(() => Date.now());
  return (
    <div className="message assistant pending-standalone" data-operation-status>
      <div className="pending-body" role="status" aria-live="polite">
        <span className="text-muted-foreground">{label}</span>
        <MessageTimestamp timestamp={timestamp ?? fallbackTimestamp} />
      </div>
    </div>
  );
}

/** claude.ai-style transcript skeleton — one user-bubble block top
 *  right, then progressively shorter grey bars. Shown while a session
 *  switch is waiting on the load_session reply (no full cache). */
export function TranscriptSkeleton() {
  return (
    <div className="transcript-skeleton" aria-hidden="true">
      <div className="skeleton-bubble" />
      <div className="skeleton-bar" style={{ width: "88%" }} />
      <div className="skeleton-bar" style={{ width: "95%" }} />
      <div className="skeleton-bar" style={{ width: "72%" }} />
      <div className="skeleton-bar" style={{ width: "90%" }} />
      <div className="skeleton-bar" style={{ width: "58%" }} />
      <div className="skeleton-bar" style={{ width: "34%" }} />
    </div>
  );
}
