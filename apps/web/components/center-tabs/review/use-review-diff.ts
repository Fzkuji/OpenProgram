import { wsRequest } from "@/lib/net/ws/ws-request";
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
