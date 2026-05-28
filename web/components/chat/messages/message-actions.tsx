"use client";

/**
 * Per-message hover action bar — React port of the legacy
 * `message-actions.js` / `-edit.js` / `-nav.js` trio.
 *
 * Sits in the bubble's `.message-header`, revealed on hover by the
 * legacy CSS (`.message:hover .message-actions`). Holds a timestamp
 * badge, Copy / Retry / Edit (user only) / Branch (assistant only)
 * buttons, and the `< N/M >` sibling-version navigator.
 *
 * Retry / Edit / Branch / checkout all hit the REST endpoints and then
 * re-request the conversation over the shared WS — the server moves
 * HEAD and `load_session` re-feeds the React store.
 */
import { useState } from "react";

import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

const SVG = {
  copy: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  ),
  check: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  ),
  retry: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <polyline points="1 20 1 14 7 14" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  ),
  branch: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  ),
  pencil: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
    </svg>
  ),
  undo: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 14 4 9 9 4" />
      <path d="M20 20v-7a4 4 0 0 0-4-4H4" />
    </svg>
  ),
  chevL: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  ),
  chevR: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  ),
};

function postJson(url: string, body: unknown): Promise<unknown> {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then((r) => {
    if (!r.ok) {
      return r.json().then((e) => {
        throw new Error(e.error || r.statusText);
      });
    }
    return r.json();
  });
}

function setRunActive(active: boolean): void {
  (
    window as unknown as { setRunActive?: (a: boolean) => void }
  ).setRunActive?.(active);
}

export function MessageActions({
  msg,
  onEdit,
}: {
  msg: ChatMsg;
  onEdit?: () => void;
}) {
  const { text: tr } = useTranslation();
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  function copy() {
    const text = msg.content || "";
    if (!text) return;
    const flash = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(flash, flash);
    } else {
      flash();
    }
  }

  function retry() {
    if (!sessionId || !msg.id || busy) return;
    setBusy(true);
    postJson("/api/chat/retry", { session_id: sessionId, msg_id: msg.id })
      .then(() => {
        setRunActive(true);
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] retry failed:", err);
        setBusy(false);
      });
  }

  function branch() {
    // Fork = move HEAD back to this message in the CURRENT session. The
    // next user turn from there creates a sibling, naturally forking the
    // DAG. Same backend op as checkout (the sibling navigator), just
    // surfaced as a separate UI action with the "diverge from this
    // point" intent. No new session.
    if (!sessionId || !msg.id || busy) return;
    setBusy(true);
    postJson("/api/chat/checkout", { session_id: sessionId, msg_id: msg.id })
      .then(() => {
        (
          window as unknown as { _postCheckoutScrollTo?: string }
        )._postCheckoutScrollTo = msg.id;
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] branch failed:", err);
        setBusy(false);
      });
  }

  function revertTurn() {
    if (!sessionId || !msg.id || busy) return;
    setBusy(true);
    const ok = wsSend({
      action: "revert_turn",
      session_id: sessionId,
      assistant_msg_id: msg.id,
    });
    if (!ok) {
      setBusy(false);
      return;
    }
    const w = window as Window & {
      ws?: WebSocket;
      __toast?: (m: string) => void;
    };
    const ws = w.ws;
    if (!ws) {
      setBusy(false);
      return;
    }
    const onMsg = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "revert_turn_result") return;
        if (data?.data?.assistant_msg_id !== msg.id) return;
        ws.removeEventListener("message", onMsg);
        const restored = data?.data?.restored_paths ?? [];
        const err = data?.data?.error;
        const text = err
          ? tr(`Revert failed: ${err}`, `撤销失败：${err}`)
          : tr(
              `Reverted ${restored.length} file${restored.length === 1 ? "" : "s"}`,
              `已撤销 ${restored.length} 个文件`,
            );
        if (w.__toast) w.__toast(text);
        else console.log("[revert]", text);
        setBusy(false);
      } catch {
        /* ignore */
      }
    };
    ws.addEventListener("message", onMsg);
  }

  function checkout(targetId: string | undefined) {
    if (!sessionId || !targetId || busy) return;
    setBusy(true);
    postJson("/api/chat/checkout", { session_id: sessionId, msg_id: targetId })
      .then(() => {
        (
          window as unknown as { _postCheckoutScrollTo?: string }
        )._postCheckoutScrollTo = targetId;
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] checkout failed:", err);
        setBusy(false);
      });
  }

  const ts = msg.timestamp
    ? new Date(msg.timestamp > 1e12 ? msg.timestamp : msg.timestamp * 1000)
    : null;
  const total = msg.siblingTotal ?? 0;
  const idx = msg.siblingIndex ?? 0;

  return (
    <div className="message-actions">
      {ts ? (
        <span className="message-timestamp" title={ts.toLocaleString()}>
          {ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </span>
      ) : null}
      <button
        type="button"
        className={"message-action-btn" + (copied ? " is-copied" : "")}
        title={tr("Copy", "复制")}
        aria-label={tr("Copy", "复制")}
        onClick={copy}
      >
        {copied ? SVG.check : SVG.copy}
      </button>
      <button
        type="button"
        className="message-action-btn"
        title={tr("Retry from here", "从这里重试")}
        aria-label={tr("Retry from here", "从这里重试")}
        disabled={busy}
        onClick={retry}
      >
        {SVG.retry}
      </button>
      {onEdit ? (
        <button
          type="button"
          className="message-action-btn"
          title={tr("Edit message", "编辑消息")}
          aria-label={tr("Edit message", "编辑消息")}
          onClick={onEdit}
        >
          {SVG.pencil}
        </button>
      ) : null}
      {msg.role === "assistant" ? (
        <button
          type="button"
          className="message-action-btn"
          title={tr("Branch into a new conversation", "分支到新会话")}
          aria-label={tr("Branch into a new conversation", "分支到新会话")}
          disabled={busy}
          onClick={branch}
        >
          {SVG.branch}
        </button>
      ) : null}
      {msg.role === "assistant" ? (
        <button
          type="button"
          className="message-action-btn"
          title={tr("Revert file edits from this turn", "撤销这一轮的文件编辑")}
          aria-label={tr("Revert file edits", "撤销文件编辑")}
          disabled={busy}
          onClick={revertTurn}
        >
          {SVG.undo}
        </button>
      ) : null}
      {total > 1 ? (
        <div className="message-nav">
          <button
            type="button"
            className="message-nav-btn"
            data-nav="prev"
            aria-label={tr("Previous version", "上一个版本")}
            disabled={busy || idx <= 1}
            onClick={() => checkout(msg.prevSiblingId)}
          >
            {SVG.chevL}
          </button>
          <span className="message-nav-label">
            {idx} / {total}
          </span>
          <button
            type="button"
            className="message-nav-btn"
            data-nav="next"
            aria-label={tr("Next version", "下一个版本")}
            disabled={busy || idx >= total}
            onClick={() => checkout(msg.nextSiblingId)}
          >
            {SVG.chevR}
          </button>
        </div>
      ) : null}
    </div>
  );
}
