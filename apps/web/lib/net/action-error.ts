import { showToast } from "@/lib/format-utils/toast";
import { isWsRequestPending } from "@/lib/net/ws/ws-request";

export interface ActionErrorNotice {
  action: string;
  code: string;
  en: string;
  zh: string;
  requestId?: string;
  sessionId?: string;
  retryable: boolean;
}

function safeMetadata(value: unknown): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  const chars = [...value];
  if (chars.length > 128) return undefined;
  if (chars.some((char) => char !== " " && /[\p{C}\p{Z}]/u.test(char))) {
    return undefined;
  }
  return value;
}

/** Normalize current and legacy command failures without exposing wire text. */
export function operationErrorNotice(
  data?: Record<string, unknown>,
): ActionErrorNotice {
  const action = safeMetadata(data?.action) ?? "?";
  const wireCode = typeof data?.code === "string" && data.code
    ? data.code
    : undefined;
  // Before action_error codes existed, the only code-less producer was the
  // unknown-action fallback. Keep that meaning while old servers are in use.
  const code = wireCode ?? "unknown_action";
  const requestId = safeMetadata(data?.request_id);
  const sessionId = safeMetadata(data?.session_id);
  const retryable = data?.retryable === true;
  if (code === "unknown_action") {
    return {
      action,
      code,
      en: `Unknown action ${action} — no backend handler`,
      zh: `未知操作 ${action} — 后端没有对应处理器`,
      requestId,
      sessionId,
      retryable,
    };
  }
  return {
    action,
    code,
    en: `Action ${action} failed`,
    zh: `操作 ${action} 失败`,
    requestId,
    sessionId,
    retryable,
  };
}

export function consumeOperationError(
  data: Record<string, unknown> | undefined,
  translate: (...variants: string[]) => string,
): ActionErrorNotice {
  const notice = operationErrorNotice(data);
  console.error(
    "[useWS] backend action failed:",
    notice.action,
    notice.code,
  );
  showToast(translate(notice.en, notice.zh), { tone: "error" });
  return notice;
}

/** Consume command-error frames at the WebSocket dispatch boundary. */
export function consumeCommandErrorFrame(
  frame: { type?: string; data?: Record<string, unknown> },
  translate: (...variants: string[]) => string,
): boolean {
  if (frame.type !== "operation_error" && frame.type !== "action_error") {
    return false;
  }
  // wsRequest owns correlated failures.  The global dispatcher must leave
  // those frames available to the request listener and avoid a duplicate
  // toast; uncorrelated errors remain user-visible here.
  if (isWsRequestPending(frame.data?.request_id, frame.data?.action)) return false;
  // A read the page sent by itself (loading a conversation, listing
  // branches, replaying a run) is not something the user did: its failure
  // is logged and the page keeps what it shows; the next read tries again.
  const action = typeof frame.data?.action === "string" ? frame.data.action : "";
  if (isBackgroundAction(action)) {
    const notice = operationErrorNotice(frame.data);
    console.warn("[useWS] background read failed:", notice.action, notice.code);
    return true;
  }
  consumeOperationError(frame.data, translate);
  return true;
}

const BACKGROUND_ACTIONS = new Set([
  "execution.replay",
  "mark_session_read",
  "project_file_operation_status",
  "reminder_shown",
  "set_attended",
  "set_window_hidden",
  "turn_operation_status",
  "webtab_closed",
  "webtab_register",
  "webtab_result",
]);

/** Actions the page sends on its own to read or report state, never as
 *  the user's own request. */
export function isBackgroundAction(action: string): boolean {
  return BACKGROUND_ACTIONS.has(action) || /^(list|get|load|read|subscribe)_/.test(action);
}

export const actionErrorNotice = operationErrorNotice;
export const consumeActionError = consumeOperationError;
