"use client";
/** The leading status marker on a conversation row: working, needs input,
 *  failed, unread or idle, drawn as one glyph slot. */

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import styles from "../sidebar.module.css";

type MarkerState = "working" | "needs_input" | "failed" | "unread" | "idle";

/** The dot to the left of a conversation title, mirroring Claude Code's
 *  status markers (colours sampled from claude.ai/code):
 *   - working      → a green spinning arc (a task is running)
 *   - pinned       → an amber pin (idle pinned rows only; running wins)
 *   - needs_input  → a filled amber dot (#ffd014 — awaiting the user)
 *   - failed       → a filled red dot (#f2615a — failed, not yet seen)
 *   - unread       → a filled blue dot (#5aa6f2 — finished, not yet seen)
 *   - idle         → a filled gray dot (done & seen / nothing pending)
 *  Fixed 16px slot so titles stay aligned regardless of state. A change
 *  of state cross-fades the two marks (scale + opacity) instead of
 *  swapping them in one frame. */
export function StatusMarker({
  pinned,
  state,
  labels,
}: {
  pinned: boolean;
  state: MarkerState;
  labels: { pinned: string; working: string; needsInput: string; failed: string; unread: string };
}) {
  const reducedMotion = useReducedMotion();
  const kind = markerKind(pinned, state);
  return (
    <span className={`${styles.convMarker} relative flex h-4 w-[16px] shrink-0`} data-marker={kind}>
      <AnimatePresence initial={false}>
        <motion.span
          key={kind}
          className="absolute inset-0 flex items-center justify-center"
          initial={{ opacity: 0, scale: 0.4 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.4 }}
          transition={{ duration: reducedMotion ? 0 : 0.2, ease: [0.2, 0.7, 0.2, 1] }}
        >
          <MarkerGlyph pinned={pinned} state={state} labels={labels} />
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

function markerKind(pinned: boolean, state: MarkerState): string {
  return state === "idle" && pinned ? "pinned" : state;
}

function MarkerGlyph({
  pinned,
  state,
  labels,
}: {
  pinned: boolean;
  state: MarkerState;
  labels: { pinned: string; working: string; needsInput: string; failed: string; unread: string };
}) {
  if (state === "working") {
    // A 3px ring with a round-capped arc on a faint track; a border's
    // top edge would end in square, clock-hand cuts.
    return (
      <svg
        className="block size-[12px] shrink-0 animate-spin"
        viewBox="0 0 12 12"
        fill="none"
        role="img"
        aria-label={labels.working}
        data-tip={labels.working}
      >
        <circle cx="6" cy="6" r="4.5" stroke="var(--status-working)" strokeOpacity={0.25} strokeWidth={3} />
        <circle
          cx="6" cy="6" r="4.5"
          stroke="var(--status-working)" strokeWidth={3} strokeLinecap="round"
          pathLength={100} strokeDasharray="22 78"
        />
      </svg>
    );
  }
  if (state === "needs_input") {
    // Awaiting the user → amber filled dot.
    return (
      <span
        className="block size-[8px] shrink-0 rounded-full bg-[color:var(--status-needs-input)]"
        aria-label={labels.needsInput}
        data-tip={labels.needsInput}
      />
    );
  }
  if (state === "failed") {
    // Failed, not yet opened → red filled dot.
    return (
      <span
        className="block size-[8px] shrink-0 rounded-full bg-[color:var(--status-failed)]"
        aria-label={labels.failed}
        data-tip={labels.failed}
      />
    );
  }
  if (state === "unread") {
    // Finished, not yet opened → blue filled dot.
    return (
      <span
        className="block size-[8px] shrink-0 rounded-full bg-[color:var(--status-unread)]"
        aria-label={labels.unread}
        data-tip={labels.unread}
      />
    );
  }
  if (pinned) {
    return (
      <svg
        className="shrink-0 text-accent-orange"
        width="12" height="12" viewBox="0 0 16 16" fill="currentColor"
        aria-label={labels.pinned}
      >
        <path d="M9.5 1.5a1 1 0 0 0-1.7.7l.1 3.2-2.4 2.4a1 1 0 0 0-.3.7v.5l2.6-.0 0 4 .8 1.3.8-1.3 0-4 2.6.0v-.5a1 1 0 0 0-.3-.7L9.5 5.4l.1-3.2a1 1 0 0 0-.1-.7z" />
      </svg>
    );
  }
  // idle / done & seen → 纯色实心点，没有描边。项目下的竖导线在点的上下
  // 断开（.convMarker），不从点里穿过。
  return (
    <span
      className="block size-[8px] shrink-0 rounded-full"
      style={{
        background: "color-mix(in srgb, var(--text-secondary) 55%, var(--bg-sidebar, var(--bg-secondary)))",
      }}
      aria-hidden="true"
    />
  );
}
