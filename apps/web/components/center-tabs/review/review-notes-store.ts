/** Review notes kept per conversation while unsent: your line comments,
 *  the agent's findings and when the running Review with agent started. A
 *  Review pane mounts again on every tab switch or merge; the notes live
 *  here so they outlast it. */
import { create } from "zustand";

import type { ReviewComment, ReviewFinding } from "./review-tab-types";

const NONE: never[] = [];

interface ReviewNotesState {
  comments: Record<string, ReviewComment[]>;
  findings: Record<string, ReviewFinding[]>;
  /** Start time (ms) of the conversation's running Review with agent; absent when none runs. */
  reviewing: Record<string, number>;
  addComment: (sessionId: string, comment: ReviewComment) => void;
  editComment: (sessionId: string, id: string, body: string) => void;
  removeComment: (sessionId: string, id: string) => void;
  clearComments: (sessionId: string) => void;
  setFindings: (sessionId: string, findings: ReviewFinding[]) => void;
  removeFinding: (sessionId: string, id: string) => void;
  setReviewing: (sessionId: string, startedAt: number | null) => void;
}

export const useReviewNotes = create<ReviewNotesState>((set) => ({
  comments: {},
  findings: {},
  reviewing: {},
  addComment: (sessionId, comment) => set((state) => ({
    comments: { ...state.comments, [sessionId]: [...(state.comments[sessionId] ?? NONE), comment] },
  })),
  editComment: (sessionId, id, body) => set((state) => ({
    comments: {
      ...state.comments,
      [sessionId]: (state.comments[sessionId] ?? NONE).map((comment) => comment.id === id ? { ...comment, body } : comment),
    },
  })),
  removeComment: (sessionId, id) => set((state) => ({
    comments: { ...state.comments, [sessionId]: (state.comments[sessionId] ?? NONE).filter((comment) => comment.id !== id) },
  })),
  clearComments: (sessionId) => set((state) => ({ comments: { ...state.comments, [sessionId]: NONE } })),
  setFindings: (sessionId, findings) => set((state) => ({ findings: { ...state.findings, [sessionId]: findings } })),
  removeFinding: (sessionId, id) => set((state) => ({
    findings: { ...state.findings, [sessionId]: (state.findings[sessionId] ?? NONE).filter((finding) => finding.id !== id) },
  })),
  setReviewing: (sessionId, startedAt) => set((state) => {
    const reviewing = { ...state.reviewing };
    if (startedAt == null) delete reviewing[sessionId];
    else reviewing[sessionId] = startedAt;
    return { reviewing };
  }),
}));

/** The review running in each conversation: its id names it to the
 *  server, and aborting its controller stops waiting for it. Kept beside
 *  the store because a pane that mounts again must still be able to stop
 *  the review an earlier mount started. */
const runningReviews = new Map<string, { reviewId: string; controller: AbortController }>();

export function startReview(sessionId: string): { reviewId: string; signal: AbortSignal } {
  const reviewId = globalThis.crypto.randomUUID();
  const controller = new AbortController();
  runningReviews.set(sessionId, { reviewId, controller });
  useReviewNotes.getState().setReviewing(sessionId, Date.now());
  return { reviewId, signal: controller.signal };
}

/** Ends the conversation's review; the id it returns lets the caller cancel it on the server. */
export function stopReview(sessionId: string): string | null {
  const running = runningReviews.get(sessionId);
  if (!running) return null;
  runningReviews.delete(sessionId);
  running.controller.abort();
  useReviewNotes.getState().setReviewing(sessionId, null);
  return running.reviewId;
}

/** A review that answered on its own: forget it unless a newer one replaced it. */
export function finishReview(sessionId: string, reviewId: string): void {
  if (runningReviews.get(sessionId)?.reviewId !== reviewId) return;
  runningReviews.delete(sessionId);
  useReviewNotes.getState().setReviewing(sessionId, null);
}

export const sessionComments = (sessionId: string) =>
  (state: ReviewNotesState): ReviewComment[] => state.comments[sessionId] ?? NONE;
export const sessionFindings = (sessionId: string) =>
  (state: ReviewNotesState): ReviewFinding[] => state.findings[sessionId] ?? NONE;
