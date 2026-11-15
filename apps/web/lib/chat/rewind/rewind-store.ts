/**
 * State of the rewind interface: the dialog's flow, the picker, each
 * conversation's marks, and what the composer owes a rewind (files to attach
 * again, or names to take back out on Undo).
 */
import { create } from "zustand";

import type { RewindMark, RewindMode, RewindPlan, RewindPoint, RewindPreview } from "./rewind-logic";

export type RewindFlow = {
  sessionId: string;
  msgId: string;
  text: string;
  plan: RewindPlan | null;
  preview: "pending" | RewindPreview;
  mode: RewindMode;
  visible: boolean;
  phase: "ready" | "running" | "slow";
  error: string | null;
  /** The branch whose turn kept a rewind that restores files from running,
   *  with the ids a Stop of it needs; the dialog offers to stop it. */
  blocking?: RewindBlocking | null;
};

export type RewindBlocking = {
  branch_id?: string | null;
  branch_name?: string;
  execution_id?: string;
  status_version?: number;
  op_id?: string;
  op_kind?: string;
  reason_code?: string;
  selected?: boolean;
};

/** Attachments to put back in the composer; `refillId` marks them as a
 *  refill's own, so Undo and the next rewind take back only those. */
export type RefillFiles = { refillId: number; files: File[] };

export type RewindPicker = { sessionId: string; points: RewindPoint[] | null };

type RewindState = {
  flow: RewindFlow | null;
  picker: RewindPicker | null;
  marks: Record<string, RewindMark[]>;
  uploading: Record<string, boolean>;
  refillFiles: Record<string, RefillFiles[]>;
  retractRefills: Record<string, number[]>;
};

export const useRewindStore = create<RewindState>(() => ({
  flow: null,
  picker: null,
  marks: {},
  uploading: {},
  refillFiles: {},
  retractRefills: {},
}));

/** The refill each conversation's composer holds and has not yet sent. */
export const lastRefill = new Map<string, { text: string; refillId: number }>();

/** The composer sent its text, so that text is no longer a refill. */
export function forgetRefill(sessionId: string): void {
  lastRefill.delete(sessionId);
}

export function patchFlow(patch: Partial<RewindFlow>): void {
  useRewindStore.setState(state => (state.flow ? { flow: { ...state.flow, ...patch } } : state));
}

export function setRewindMarks(sessionId: string, marks: RewindMark[]): void {
  useRewindStore.setState(state => ({ marks: { ...state.marks, [sessionId]: marks } }));
}

export function setRewindUploading(sessionId: string, uploading: boolean): void {
  if (Boolean(useRewindStore.getState().uploading[sessionId]) === uploading) return;
  useRewindStore.setState(state => ({ uploading: { ...state.uploading, [sessionId]: uploading } }));
}

export function takeRefillFiles(sessionId: string): RefillFiles[] | undefined {
  const files = useRewindStore.getState().refillFiles[sessionId];
  if (!files) return undefined;
  useRewindStore.setState(state => {
    const refillFiles = { ...state.refillFiles };
    delete refillFiles[sessionId];
    return { refillFiles };
  });
  return files;
}

export function takeRetractRefills(sessionId: string): number[] | undefined {
  const ids = useRewindStore.getState().retractRefills[sessionId];
  if (!ids) return undefined;
  useRewindStore.setState(state => {
    const retractRefills = { ...state.retractRefills };
    delete retractRefills[sessionId];
    return { retractRefills };
  });
  return ids;
}

/** Ask the composer to drop a refill's attachments. Those still queued are
 *  dropped here; the composer removes the ones it already holds. */
export function requestRefillRetract(sessionId: string, refillId: number): void {
  useRewindStore.setState(state => ({
    refillFiles: {
      ...state.refillFiles,
      [sessionId]: (state.refillFiles[sessionId] ?? []).filter(group => group.refillId !== refillId),
    },
    retractRefills: {
      ...state.retractRefills,
      [sessionId]: [...(state.retractRefills[sessionId] ?? []), refillId],
    },
  }));
}

/** Which of a composer's items belong to these refills. */
export function refillItemIds(items: readonly { id: string; refillId?: number }[], refills: readonly number[]): string[] {
  return items.filter(item => item.refillId !== undefined && refills.includes(item.refillId)).map(item => item.id);
}
