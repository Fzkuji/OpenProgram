import { wsRequest } from "@/lib/net/ws-request";
import type { ReviewCategory, ReviewScope, ReviewSort, ScopeState } from "./review-tab-types";

const TIMEOUT_MS = 10_000;

export function requestReviewScope(
  values: {
    sessionId: string;
    assistantMsgId?: string;
    scope: ReviewScope;
    category: ReviewCategory;
    query: string;
    sort: ReviewSort;
    cursor: string | null;
    snapshotId?: string;
    signal: AbortSignal;
  },
): Promise<(ScopeState & Record<string, unknown>) | null> {
  return wsRequest<ScopeState & Record<string, unknown>>(
    "review_scope",
    {
      session_id: values.sessionId,
      assistant_msg_id: values.assistantMsgId,
      scope: values.scope,
      category: values.category,
      query: values.query,
      sort: values.sort,
      cursor: values.cursor,
      limit: 100,
      ...(values.cursor && values.snapshotId ? { snapshot_id: values.snapshotId } : {}),
    },
    "review_scope_result",
    (value) => value.session_id === values.sessionId
      && value.scope === values.scope
      && (values.scope !== "turn" || value.assistant_msg_id === values.assistantMsgId)
      && value.category === values.category
      && value.query === values.query
      && value.sort === values.sort,
    TIMEOUT_MS,
    { signal: values.signal },
  );
}

