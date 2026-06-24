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
import {
  cloneElement,
  isValidElement,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  CheckIcon,
  CopyIcon,
  GitBranchIcon,
  RefreshCwIcon,
  SquarePenIcon,
  UndoIcon,
} from "@/components/animated-icons";

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

// Tools / action glyphs are the animated line icons (pqoqubbw, in
// ../../animated-icons). They self-animate on hover (uncontrolled) —
// the action button is icon-sized so hovering it animates the glyph.
// chevL/chevR stay static (tiny ‹ › nav carets — not worth animating).
const SVG = {
  copy: <CopyIcon />,
  check: <CheckIcon />,
  retry: <RefreshCwIcon />,
  branch: <GitBranchIcon />,
  pencil: <SquarePenIcon />,
  undo: <UndoIcon />,
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

/**
 * A message-action button whose animated icon is driven by the WHOLE
 * button's hover, not the glyph's small hit area. We clone the icon with
 * a ref and start/stop it from the button's onMouseEnter/Leave — so the
 * entire 26px button is the hover target and the animation replays
 * reliably every time. (Uncontrolled self-hover only fired over the
 * centred 14px glyph and could miss the second hover.)
 */
function ActionButton({
  icon,
  title,
  onClick,
  disabled,
  extraClass,
}: {
  icon: ReactNode;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  extraClass?: string;
}) {
  const ref = useRef<AnimatedNavIconHandle>(null);
  const node = isValidElement(icon)
    ? cloneElement(icon as ReactElement, { ref } as Record<string, unknown>)
    : icon;
  return (
    <button
      type="button"
      className={"message-action-btn" + (extraClass ? " " + extraClass : "")}
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => ref.current?.startAnimation?.()}
      onMouseLeave={() => ref.current?.stopAnimation?.()}
    >
      {node}
    </button>
  );
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

  function rewindToHere() {
    if (!sessionId || !msg.id || busy) return;
    setBusy(true);
    const ok = wsSend({
      action: "rewind",
      session_id: sessionId,
      msg_id: msg.id,
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
        if (data?.type === "rewind_result") {
          if (data?.data?.assistant_msg_id && data.data.assistant_msg_id !== msg.id) return;
          ws.removeEventListener("message", onMsg);
          const restored = data?.data?.restored_paths ?? [];
          const err = data?.data?.error;
          const text = err
            ? tr(`Rewind failed: ${err}`, `回退失败：${err}`)
            : tr(
                `Rewound ${restored.length} file${restored.length === 1 ? "" : "s"}`,
                `已回退 ${restored.length} 个文件`,
              );
          if (w.__toast) w.__toast(text);
          else console.log("[rewind]", text);
          setBusy(false);
        }
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
      <ActionButton
        icon={copied ? SVG.check : SVG.copy}
        title={tr("Copy", "复制")}
        extraClass={copied ? "is-copied" : undefined}
        onClick={copy}
      />
      <ActionButton
        icon={SVG.retry}
        title={tr("Retry from here", "从这里重试")}
        disabled={busy}
        onClick={retry}
      />
      {onEdit ? (
        <ActionButton
          icon={SVG.pencil}
          title={tr("Edit message", "编辑消息")}
          onClick={onEdit}
        />
      ) : null}
      {msg.role === "assistant" ? (
        <ActionButton
          icon={SVG.branch}
          title={tr("Branch into a new conversation", "分支到新会话")}
          disabled={busy}
          onClick={branch}
        />
      ) : null}
      {msg.role === "user" ? (
        <ActionButton
          icon={SVG.undo}
          title={tr("Rewind to here", "回退到这里")}
          disabled={busy}
          onClick={rewindToHere}
        />
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
