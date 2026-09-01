import { showToast } from "@/lib/format-utils/toast";

export interface ActionErrorNotice {
  action: string;
  code: string;
  en: string;
  zh: string;
}

/** Classify legacy ``action_error`` frames without exposing raw error text. */
export function actionErrorNotice(
  data?: Record<string, unknown>,
): ActionErrorNotice {
  const action = typeof data?.action === "string" && data.action
    ? data.action
    : "?";
  const wireCode = typeof data?.code === "string" && data.code
    ? data.code
    : undefined;
  // Before action_error codes existed, the only code-less producer was the
  // unknown-action fallback. Keep that meaning while old servers are in use.
  const code = wireCode ?? "unknown_action";
  if (code === "unknown_action") {
    return {
      action,
      code,
      en: `Unknown action ${action} — no backend handler`,
      zh: `未知操作 ${action} — 后端没有对应处理器`,
    };
  }
  return {
    action,
    code,
    en: `Action ${action} failed`,
    zh: `操作 ${action} 失败`,
  };
}

export function consumeActionError(
  data: Record<string, unknown> | undefined,
  translate: (...variants: string[]) => string,
): ActionErrorNotice {
  const notice = actionErrorNotice(data);
  console.error(
    "[useWS] backend action failed:",
    notice.action,
    notice.code,
  );
  showToast(translate(notice.en, notice.zh), { tone: "error" });
  return notice;
}
