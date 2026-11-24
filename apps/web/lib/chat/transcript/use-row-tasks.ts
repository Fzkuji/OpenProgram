"use client";

/** The task segments one reply draws, and the way to a task block on the page. */
import { useMemo } from "react";

import { selectViewRows, useMessageIds, useSessionStore } from "@/lib/session-store";
import { announceReaderSeek } from "@/lib/chat/history/chat-scroll";
import { setTaskOpen, taskOpenKey } from "@/lib/prefs/task-block-open";
import { NO_ROW_TASKS, rowTasks, useTaskStore, type RowTasks, type TaskSegment } from "./task-blocks";

const NO_SEGMENTS: TaskSegment[] = [];
export function useRowTasks(sessionId: string | undefined, viewKey: string | undefined, rowId: string): RowTasks {
  const segments = useTaskStore((s) => (sessionId ? s.segmentsBySession[sessionId] : undefined) ?? NO_SEGMENTS);
  // The row sequence, and only when there are tasks to place: the store's
  // own array is replaced by every write to the view, so a reply reading it
  // would redraw on each streamed token of another reply.
  const order = useMessageIds(segments.length && viewKey ? viewKey : null);
  return useMemo(
    () => (segments.length
      ? rowTasks(segments, order, selectViewRows(viewKey)(useSessionStore.getState()), rowId)
      : NO_ROW_TASKS),
    [segments, order, viewKey, rowId],
  );
}

const esc = (value: string) => (typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : value);

/** The drawn block of a task under a reply, when that reply is mounted. */
export function taskBlockElement(taskId: string, anchorId: string, root: ParentNode = document): HTMLElement | null {
  return root.querySelector<HTMLElement>(`[data-task-id="${esc(taskId)}"][data-anchor="${esc(anchorId)}"]`);
}

/** Scroll to a task block, opening it when it is folded; to its reply when
 *  the block itself is not mounted. False when neither is on the page.
 *
 *  Opening is recorded as the reader's own choice, which then holds for
 *  good, so a block that is already open is only scrolled to: it still
 *  folds by itself when its task ends. `openUnmounted: false` says the
 *  same of a block that is not on the page (the task still running). */
export function revealTask(
  taskId: string,
  anchorId: string,
  root: ParentNode = document,
  openUnmounted = true,
): boolean {
  const block = taskBlockElement(taskId, anchorId, root);
  const target = block
    ?? root.querySelector<HTMLElement>(`[data-msg-id="${esc(anchorId)}"]`)
    ?? root.querySelector<HTMLElement>(`[data-msg-slot="${esc(anchorId)}"]`);
  if (!target) return false;
  if (block ? block.dataset.open === "0" : openUnmounted) setTaskOpen(anchorId, taskOpenKey(taskId), true);
  announceReaderSeek(target);
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  return true;
}

/** Scroll to one step of a reply (a tool call, by its block id): its task
 *  block is opened first, and the step is scrolled to when it is drawn; a
 *  step inside a folded strip is not, and the reader lands on the task
 *  block, or on the reply for a call outside any task. */
export function revealBlock(rowId: string, blockId: string, taskId?: string, root: ParentNode = document): boolean {
  const reply = () => root.querySelector<HTMLElement>(`[data-msg-id="${esc(rowId)}"]`)
    ?? root.querySelector<HTMLElement>(`[data-msg-slot="${esc(rowId)}"]`);
  const step = () => reply()?.querySelector<HTMLElement>(`[data-block-id="${esc(blockId)}"]`) ?? null;
  const go = (target: HTMLElement) => {
    announceReaderSeek(target);
    target.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const landed = taskId ? revealTask(taskId, rowId, root) : false;
  const now = step();
  if (now) { go(now); return true; }
  if (!landed) {
    const target = reply();
    if (!target) return false;
    go(target);
  }
  // Opening the task block draws its steps a frame later.
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => requestAnimationFrame(() => { const drawn = step(); if (drawn) go(drawn); }));
  }
  return true;
}
