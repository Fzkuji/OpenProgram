"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useReducedMotion } from "framer-motion";
import { useAgents } from "@/lib/agents/agents-query";
import { selectViewRows, useSessionStore } from "@/lib/session-store";
import { botTimeLine } from "@/lib/chat/transcript/bot-view";
import { useTranslation } from "@/lib/i18n";

const FADE_MS = 270;
/** The fold starts while the steps are still fading, and the time labels
 *  arrive with it, so the height only ever moves one way. */
const COLLAPSE_START_MS = 140;
const COLLAPSE_MS = 360;
const COLLAPSE_EASE = "cubic-bezier(0.2, 0, 0, 1)";
/** The view a conversation settles into while it loads is not a switch. */
const LOAD_SETTLE_MS = 800;

/** Only a change made in a conversation already on screen plays the switch. */
export function playsBotSwitch(input: {
  sameSession: boolean; agentsLoaded: boolean; conversationKnown: boolean; msSinceOpened: number; reducedMotion: boolean;
}): boolean {
  return input.sameSession && input.agentsLoaded && input.conversationKnown
    && !input.reducedMotion && input.msSinceOpened >= LOAD_SETTLE_MS;
}

/** What the chat view leaves out of the normal view. */
function partsOnlyTheFullViewShows(root: HTMLElement): HTMLElement[] {
  const parts = [...root.querySelectorAll<HTMLElement>("[data-only-full-view], .message:not(.user):not(.assistant)")];
  root.querySelectorAll<HTMLElement>(".message.assistant").forEach((row) => {
    const words = row.querySelector<HTMLElement>(".chat-stream-body > .chat-text");
    if (!words || !words.textContent?.trim()) { parts.push(row); return; }
    row.querySelectorAll<HTMLElement>(":scope > .chat-stream-body > :not(.chat-text), :scope > .message-actions-footer")
      .forEach((part) => parts.push(part));
  });
  return parts;
}

type Box = Record<"height" | "marginTop" | "marginBottom" | "paddingTop" | "paddingBottom", string>;
const SHUT: Box = { height: "0px", marginTop: "0px", marginBottom: "0px", paddingTop: "0px", paddingBottom: "0px" };

function boxOf(part: HTMLElement): Box {
  const style = getComputedStyle(part);
  return { height: `${part.getBoundingClientRect().height}px`, marginTop: style.marginTop, marginBottom: style.marginBottom,
    paddingTop: style.paddingTop, paddingBottom: style.paddingBottom };
}

const TOTAL_MS = COLLAPSE_START_MS + COLLAPSE_MS;
const FOLD_AT = COLLAPSE_START_MS / TOTAL_MS;
const FADED_AT = FADE_MS / TOTAL_MS;

/** Fade and fold, or (reversed) unfold and fade. Native animations keep no
 *  state on the element, so cancelling one leaves the row as it was. */
function playPart(part: HTMLElement, closing: boolean): Animation {
  const box = boxOf(part);
  const frames = closing
    ? [{ ...box, opacity: 1, overflow: "hidden", offset: 0 },
      { ...box, offset: FOLD_AT, easing: COLLAPSE_EASE }, { opacity: 0, offset: FADED_AT },
      { ...SHUT, opacity: 0, overflow: "hidden", offset: 1 }]
    : [{ ...SHUT, opacity: 0, overflow: "hidden", offset: 0, easing: COLLAPSE_EASE },
      { opacity: 0, offset: 1 - FADED_AT }, { ...box, offset: 1 - FOLD_AT },
      { ...box, opacity: 1, overflow: "hidden", offset: 1 }];
  return part.animate(frames, { duration: TOTAL_MS, fill: "both" });
}

/** The chat rows are not there yet (or any more) while the normal rows move,
 *  so stand-in time labels arrive and leave with the steps. */
function playTimeStandIns(
  root: HTMLElement, moved: HTMLElement[], labels: ReadonlyMap<string, number>, lineOf: (id: string, at: number) => string, closing: boolean,
): { standIns: HTMLElement[]; animations: Animation[] } {
  const rows = [...root.querySelectorAll<HTMLElement>(".message[data-msg-id]")]
    .filter((row) => labels.has(row.dataset.msgId ?? "") && !moved.includes(row));
  const standIns: HTMLElement[] = [];
  const place = (label: HTMLElement) => {
    standIns.push(label);
    const box = boxOf(label);
    const frames = closing
      ? [{ ...SHUT, opacity: 0, overflow: "hidden", offset: 0 }, { ...SHUT, offset: FOLD_AT, easing: COLLAPSE_EASE },
        { opacity: 1, offset: FADED_AT }, { ...box, opacity: 1, overflow: "hidden", offset: 1 }]
      : [{ ...box, opacity: 1, overflow: "hidden", offset: 0, easing: COLLAPSE_EASE }, { opacity: 0, offset: 1 - FADED_AT },
        { ...SHUT, offset: 1 - FOLD_AT }, { ...SHUT, opacity: 0, overflow: "hidden", offset: 1 }];
    return label.animate(frames, { duration: TOTAL_MS, fill: "both" });
  };
  const animations = rows.map((row) => {
    const label = document.createElement("div");
    label.className = "bot-time";
    label.textContent = lineOf(row.dataset.msgId ?? "", labels.get(row.dataset.msgId ?? "") ?? 0);
    row.before(label);
    return place(label);
  });
  // The room the normal view keeps under a last reply for its action buttons.
  const final = [...root.querySelectorAll<HTMLElement>(".message[data-msg-id]")].at(-1);
  if (final?.classList.contains("assistant")) {
    const room = document.createElement("div");
    room.className = "bot-end-room";
    final.after(room);
    animations.push(place(room));
  }
  return { standIns, animations };
}

/**
 * The view to draw for a conversation whose ``target`` view may change.
 * Both directions animate on the normal rows, which can take the chat look
 * by style alone; the chat rows replace them once the look has landed.
 */
export function useBotViewTransition(
  sessionId: string | null,
  target: boolean,
): { shown: boolean; markerRef: React.RefObject<HTMLSpanElement>; labelledRows: React.MutableRefObject<ReadonlyMap<string, number>> } {
  const labelledRows = useRef<ReadonlyMap<string, number>>(new Map());
  const markerRef = useRef<HTMLSpanElement>(null);
  const [shown, setShown] = useState(target);
  const reducedMotion = useReducedMotion() ?? false;
  const { locale, text } = useTranslation();
  const timeLine = (id: string, at: number) =>
    botTimeLine(selectViewRows(sessionId)(useSessionStore.getState())[id], at, new Date(), locale, text);
  const agentsLoaded = useAgents().data !== undefined;
  const conversationKnown = useSessionStore((state) => Boolean(sessionId && state.conversations?.[sessionId]));
  const opened = useRef({ sessionId, at: performance.now() });
  // What the view could rest on before this commit: data that lands in the
  // same commit as a change explains that change, so it is loading.
  const settled = useRef({ agentsLoaded, conversationKnown });
  const moving = useRef<{ root: HTMLElement; animations: Animation[]; standIns: HTMLElement[] } | null>(null);
  const entering = useRef(false);
  const run = useRef(0);

  const stopMoving = () => {
    const current = moving.current;
    if (!current) return;
    moving.current = null;
    current.animations.forEach((animation) => animation.cancel());
    current.standIns.forEach((label) => label.remove());
    delete current.root.dataset.botLook;
    delete current.root.dataset.botMoving;
  };

  useLayoutEffect(() => {
    const sameSession = opened.current.sessionId === sessionId;
    if (!sameSession) opened.current = { sessionId, at: performance.now() };
    const before = settled.current;
    settled.current = { agentsLoaded, conversationKnown };
    const token = ++run.current;
    stopMoving();
    if (target === shown) return;
    const root = markerRef.current?.parentElement ?? null;
    const plays = root !== null && playsBotSwitch({
      sameSession, reducedMotion, agentsLoaded: before.agentsLoaded, conversationKnown: before.conversationKnown,
      msSinceOpened: performance.now() - opened.current.at,
    });
    if (!plays) { entering.current = false; setShown(target); return; }
    if (!target) { entering.current = true; setShown(false); return; }

    root.dataset.botMoving = "1";
    root.dataset.botLook = "1";
    const parts = partsOnlyTheFullViewShows(root);
    const times = playTimeStandIns(root, parts, labelledRows.current, timeLine, true);
    const animations = [...parts.map((part) => playPart(part, true)), ...times.animations];
    moving.current = { root, animations, standIns: times.standIns };
    void Promise.all(animations.map((animation) => animation.finished)).then(() => {
      if (token !== run.current) return;
      // The chat rows take over in this same frame; only then may the look go.
      flushSync(() => setShown(true));
    }, () => undefined);
  }, [sessionId, target, shown, agentsLoaded, conversationKnown, reducedMotion]);

  // Back to the normal rows: they start in the chat look with the steps shut.
  useLayoutEffect(() => {
    const root = markerRef.current?.parentElement ?? null;
    if (!entering.current || shown || !root) return;
    entering.current = false;
    const token = ++run.current;
    const parts = partsOnlyTheFullViewShows(root);
    const times = playTimeStandIns(root, parts, labelledRows.current, timeLine, false);
    const animations = [...parts.map((part) => playPart(part, false)), ...times.animations];
    root.dataset.botLook = "1";
    void root.offsetHeight;
    root.dataset.botMoving = "1";
    delete root.dataset.botLook;
    moving.current = { root, animations, standIns: times.standIns };
    void Promise.all(animations.map((animation) => animation.finished)).then(() => {
      if (token === run.current) stopMoving();
    }, () => undefined);
  }, [shown]);

  useEffect(() => stopMoving, []);

  return { shown, markerRef, labelledRows };
}
