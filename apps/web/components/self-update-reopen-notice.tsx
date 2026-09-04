"use client";

import { useEffect, useState } from "react";
import type { DesktopReopenState } from "@/lib/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import styles from "./self-update-reopen-notice.module.css";

const REASONS: Record<string, [string, string]> = {
  owner_mismatch: ["Owner authentication changed or failed.", "所有者认证已改变或失败。"],
  owner_auth_unavailable: ["Owner authentication is unavailable.", "所有者认证不可用。"],
  update_missing: ["The update record is missing.", "更新记录不存在。"],
  intent_missing: ["The recovery request is missing.", "恢复请求不存在。"],
  intent_expired: ["The recovery request expired.", "恢复请求已过期。"],
  session_missing: ["The original conversation no longer exists.", "原会话已不存在。"],
  origin_missing: ["The original update message no longer exists.", "原更新消息已不存在。"],
  activation_not_started: ["Installation has not started.", "安装尚未开始。"],
  launch_argument_invalid: ["The recovery launch argument is invalid.", "恢复启动参数无效。"],
  intent_invalid: ["The recovery request identity is invalid.", "恢复请求身份无效。"],
  state_invalid: ["The saved recovery state could not be validated.", "保存的恢复状态未通过校验。"],
  response_invalid: ["The recovery response could not be validated.", "恢复响应未通过校验。"],
  ack_invalid: ["The saved loading confirmation is invalid.", "保存的加载确认无效。"],
  ack_identity_mismatch: ["The loading confirmation identity changed.", "加载确认身份已改变。"],
};

export function SelfUpdateReopenNotice() {
  const { text } = useTranslation();
  const [state, setState] = useState<DesktopReopenState | null>(null);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    const bridge = window.openprogramDesktop;
    if (bridge?.windowId !== "main" || !bridge.selfUpdateReopen) return;
    let active = true;
    let receivedEvent = false;
    const stop = bridge.selfUpdateReopen.onState((value) => {
      receivedEvent = true;
      if (active) setState(value);
    });
    void bridge.selfUpdateReopen.getState().then((value) => {
      if (active && !receivedEvent) setState(value);
    }).catch(() => {
      if (active && !receivedEvent) setState({ updateId: null, sessionId: null,
        status: "unavailable", reason: "recovery_unavailable" });
    });
    return () => { active = false; stop(); };
  }, []);
  if (!state || state.status === "inactive" || dismissed) return null;
  const explanation = state.status === "manual_navigation"
    ? text("You changed pages; automatic conversation recovery stopped.", "你已切换页面，自动会话定位已停止。")
    : state.status === "acknowledged"
      ? text("The original conversation loaded. Update verification is separate.", "原会话已加载，更新验收另行执行。")
      : state.status === "pending"
        ? text("Waiting for the original conversation to load.", "等待原会话加载。")
        : text(...(REASONS[state.reason ?? ""] ?? ["Conversation recovery is unavailable.", "会话恢复暂不可用。"]));
  const sessionId = state.sessionId && /^[A-Za-z0-9_-]{1,256}$/.test(state.sessionId) ? state.sessionId : null;
  return <aside className={styles.notice} role="status" aria-label={text("Self-update conversation recovery", "自更新会话恢复")}>
    <span>{explanation}</span>
    {sessionId && <a href={`/s/${sessionId}`}>{text("Open original conversation", "打开原会话")}</a>}
    <button type="button" onClick={() => setDismissed(true)}>{text("Dismiss", "关闭提示")}</button>
  </aside>;
}
