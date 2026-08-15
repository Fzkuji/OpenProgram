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
import { showToast } from "@/lib/format-utils/toast";
import { setRunActive } from "@/lib/runtime-bridge/chat-handlers";
import { getSocket, runtimeState } from "@/lib/runtime-bridge/state";
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
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  sock.send(JSON.stringify(payload));
  return true;
}

// Tools / action glyphs are the animated line icons (pqoqubbw, in
// ../../animated-icons). They self-animate on hover (uncontrolled) —
// the action button is icon-sized so hovering it animates the glyph.
// chevL/chevR stay static (tiny ‹ › nav carets — not worth animating).
export const SVG = {
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

/**
 * A message-action button whose animated icon is driven by the WHOLE
 * button's hover, not the glyph's small hit area. We clone the icon with
 * a ref and start/stop it from the button's onMouseEnter/Leave — so the
 * entire 26px button is the hover target and the animation replays
 * reliably every time. (Uncontrolled self-hover only fired over the
 * centred 14px glyph and could miss the second hover.)
 */
export function ActionButton({
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

export function MessageTimestamp({ timestamp }: { timestamp?: number }) {
  if (
    typeof timestamp !== "number"
    || !Number.isFinite(timestamp)
    || timestamp <= 0
  ) return null;
  const value = new Date(timestamp > 1e12 ? timestamp : timestamp * 1000);
  const fullTime = value.toLocaleString();
  return (
    <span
      className="message-timestamp"
      title={fullTime}
      aria-label={fullTime}
      tabIndex={0}
    >
      {value.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
    </span>
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
        // The turn result must NOT be pushed onto the pre-fork mirror:
        // flag the session so the result handler reloads wholesale
        // (chat-handlers self-heal) — this load and the stream race.
        runtimeState._pendingBranchReload[sessionId] = true;
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
        runtimeState._postCheckoutScrollTo = msg.id;
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
      target_msg_id: msg.id,
    });
    if (!ok) {
      setBusy(false);
      return;
    }
    const ws = getSocket();
    if (!ws) {
      setBusy(false);
      return;
    }
    // Every exit path — reply, wrong session, timeout — runs through
    // `finish` so the listener is always detached and `busy` always
    // clears. A rewind that errors server-side without ever emitting a
    // frame used to leave this row's buttons disabled until a reload.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      ws.removeEventListener("message", onMsg);
      if (timer) clearTimeout(timer);
      setBusy(false);
    };
    function onMsg(ev: MessageEvent) {
      try {
        const data = JSON.parse(ev.data);
        if (data?.type !== "rewind_result") return;
        const d = data?.data ?? {};
        // Match on the session the frame belongs to: a rewind running in
        // another conversation emits the same frame type, and answering
        // to it here would reload + prefill the wrong chat.
        if (d.session_id && d.session_id !== sessionId) return;
        finish();
        const errors: string[] = d.errors ?? [];
        // `new_head_id` decides success: the backend moves HEAD whenever
        // it is set, and a non-empty `errors` then only means some files
        // failed to restore. Treating that as overall failure would leave
        // the transcript on a head the server no longer has.
        if (d.new_head_id) {
          const n = d.turns_reverted ?? 0;
          if (errors.length) {
            showToast(
              tr(
                `Rewound ${n} turn${n === 1 ? "" : "s"}, but ${errors.length} file${errors.length === 1 ? "" : "s"} failed to restore: ${errors[0]}`,
                `已回退 ${n} 轮，但 ${errors.length} 个文件恢复失败：${errors[0]}`,
              ),
              { tone: "warn" },
            );
          } else {
            showToast(tr(`Rewound ${n} turn${n === 1 ? "" : "s"}`, `已回退 ${n} 轮对话`));
          }
          // Prefill composer with the rewound user message
          if (d.user_text) {
            useSessionStore.getState().setComposerInput(d.user_text);
          }
          // Reload session so rewound messages disappear
          wsSend({ action: "load_session", session_id: sessionId });
        } else {
          const err = d.error ?? (errors.length ? errors.join("; ") : "unknown error");
          showToast(tr(`Rewind failed: ${err}`, `回退失败：${err}`));
        }
      } catch {
        /* ignore */
      }
    }
    ws.addEventListener("message", onMsg);
    // ponytail: fixed 30s ceiling. A rewind touching thousands of backed-up
    // files could in principle outrun it; make it adaptive if that shows up.
    timer = setTimeout(() => {
      finish();
      showToast(tr("Rewind timed out", "回退超时，未收到结果"), { tone: "error" });
    }, 30_000);
  }

  function checkout(targetId: string | undefined, dir: -1 | 1) {
    if (!sessionId || !targetId || busy) return;
    setBusy(true);
    // 0ms feedback (interaction-feedback policy): advance the < N/M >
    // label to the target version immediately so the click registers,
    // rather than waiting the checkout POST + load_session round-trip that
    // swaps the whole transcript. The reload rebuilds this row from the
    // target branch (its content backfills); on failure we revert the
    // label + re-enable the nav.
    const store = useSessionStore.getState();
    const prevIdx = msg.siblingIndex;
    store.updateMessage(sessionId, msg.id, {
      siblingIndex: (msg.siblingIndex ?? 0) + dir,
    });
    postJson("/api/chat/checkout", { session_id: sessionId, msg_id: targetId })
      .then(() => {
        runtimeState._postCheckoutScrollTo = targetId;
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] checkout failed:", err);
        if (useSessionStore.getState().messagesById[msg.id]) {
          useSessionStore
            .getState()
            .updateMessage(sessionId, msg.id, { siblingIndex: prevIdx });
        }
        showToast(tr("Version switch failed.", "版本切换失败。"), { tone: "error" });
        setBusy(false);
      });
  }

  const total = msg.siblingTotal ?? 0;
  const idx = msg.siblingIndex ?? 0;

  return (
    <div className="message-actions">
      <MessageTimestamp timestamp={msg.timestamp} />
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
            onClick={() => checkout(msg.prevSiblingId, -1)}
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
            onClick={() => checkout(msg.nextSiblingId, 1)}
          >
            {SVG.chevR}
          </button>
        </div>
      ) : null}
    </div>
  );
}
