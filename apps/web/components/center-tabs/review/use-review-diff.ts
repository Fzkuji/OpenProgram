import { onReconnected } from "@/lib/net/ws/change-bus";
import { wsFire, wsRequest } from "@/lib/net/ws/ws-request";
import { getSocket } from "@/lib/runtime-bridge/state";
import type { DiffState, ReviewCategory, ReviewScope, ReviewSort } from "./review-tab-types";
import type { ReviewHunk } from "./review-diff-rows.ts";

const TIMEOUT_MS = 10_000;

export function requestReviewDiff(
  values: {
    sessionId: string;
    assistantMsgId?: string;
    scope: ReviewScope;
    category: ReviewCategory;
    query: string;
    sort: ReviewSort;
    path: string;
    cursor: string | null;
    snapshotId: string;
    signal: AbortSignal;
  },
): Promise<(DiffState & Record<string, unknown>) | null> {
  return wsRequest<DiffState & Record<string, unknown>>(
    "review_file_diff",
    {
      session_id: values.sessionId,
      assistant_msg_id: values.assistantMsgId,
      scope: values.scope,
      category: values.category,
      query: values.query,
      sort: values.sort,
      path: values.path,
      cursor: values.cursor,
      snapshot_id: values.snapshotId,
    },
    "review_file_diff_result",
    (value) => value.session_id === values.sessionId
      && value.scope === values.scope
      && value.path === values.path
      && value.snapshot_id === values.snapshotId
      && value.category === values.category
      && value.query === values.query
      && value.sort === values.sort,
    TIMEOUT_MS,
    { signal: values.signal },
  );
}

export interface RevertHunkResult {
  status?: string;
  error_code?: string;
  error?: string;
}

/** Put one hunk back to its old side in the working file. */
export function requestRevertHunk(values: {
  sessionId: string;
  assistantMsgId?: string;
  scope: ReviewScope;
  category: ReviewCategory;
  query: string;
  sort: ReviewSort;
  path: string;
  snapshotId: string;
  hunk: ReviewHunk;
}): Promise<RevertHunkResult | null> {
  const { new_start, old_lines, new_lines } = values.hunk;
  return wsRequest<RevertHunkResult>(
    "review_revert_hunk",
    {
      session_id: values.sessionId,
      assistant_msg_id: values.assistantMsgId,
      scope: values.scope,
      category: values.category,
      query: values.query,
      sort: values.sort,
      path: values.path,
      snapshot_id: values.snapshotId,
      hunk: { new_start, old_lines, new_lines },
      idempotency_key: globalThis.crypto.randomUUID(),
    },
    "review_revert_hunk_result",
    { requestId: true },
    TIMEOUT_MS,
  );
}

/** Stage (Unstaged scope) or unstage (Staged scope) one hunk, or the
 *  whole file without a hunk. */
export function requestStage(values: {
  sessionId: string;
  scope: ReviewScope;
  category: ReviewCategory;
  query: string;
  sort: ReviewSort;
  path: string;
  snapshotId: string;
  hunk?: ReviewHunk;
}): Promise<RevertHunkResult | null> {
  const hunk = values.hunk
    ? { old_start: values.hunk.old_start, new_start: values.hunk.new_start, lines: values.hunk.lines }
    : undefined;
  return wsRequest<RevertHunkResult>(
    "review_stage",
    {
      session_id: values.sessionId,
      scope: values.scope,
      category: values.category,
      query: values.query,
      sort: values.sort,
      path: values.path,
      snapshot_id: values.snapshotId,
      ...(hunk ? { hunk } : {}),
      idempotency_key: globalThis.crypto.randomUUID(),
    },
    "review_stage_result",
    { requestId: true },
    TIMEOUT_MS,
  );
}

export interface AgentReviewResult {
  status?: string;
  error_code?: string;
  findings?: { path: string; line: number; severity: "low" | "medium" | "high"; body: string }[];
}

/** A review of a large change takes minutes on a slow model, so the
 *  answer is waited for as long as it may take. */
const REVIEW_TIMEOUT_MS = 30 * 60_000;
/** How many times a dropped socket is waited out before giving up. */
const REVIEW_RECONNECTS = 6;

/** Resolves once the socket is open again, or when `signal` aborts. */
function socketBack(signal: AbortSignal): Promise<void> {
  if (getSocket()?.readyState === WebSocket.OPEN) return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      stop();
      signal.removeEventListener("abort", done);
      resolve();
    };
    const stop = onReconnected(done);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** Ask the session's model to review the scope the pane shows. The answer
 *  is kept on the server under `reviewId`, so a socket that drops while the
 *  model works comes back and asks for the same review again. */
export async function requestAgentReview(values: {
  sessionId: string;
  assistantMsgId?: string;
  scope: ReviewScope;
  category: ReviewCategory;
  query: string;
  sort: ReviewSort;
  snapshotId: string;
  /** Names the review so Stop review can end it and a reconnect can find it. */
  reviewId: string;
  signal: AbortSignal;
}): Promise<AgentReviewResult | null> {
  let result = await wsRequest<AgentReviewResult>(
    "review_agent",
    {
      review_id: values.reviewId,
      session_id: values.sessionId,
      assistant_msg_id: values.assistantMsgId,
      scope: values.scope,
      category: values.category,
      query: values.query,
      sort: values.sort,
      snapshot_id: values.snapshotId,
    },
    "review_agent_result",
    { requestId: true, signal: values.signal },
    REVIEW_TIMEOUT_MS,
  );
  for (let tries = 0; result === null && !values.signal.aborted && tries < REVIEW_RECONNECTS; tries += 1) {
    await socketBack(values.signal);
    if (values.signal.aborted) break;
    result = await wsRequest<AgentReviewResult>(
      "review_agent_wait",
      { review_id: values.reviewId, session_id: values.sessionId },
      "review_agent_result",
      { requestId: true, signal: values.signal },
      REVIEW_TIMEOUT_MS,
    );
  }
  return result;
}

/** Stop review: the server cancels the review and its model request. */
export function cancelAgentReview(reviewId: string): void {
  wsFire("review_agent_cancel", { review_id: reviewId });
}
