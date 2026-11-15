"use client";
import { adoptCheckedOutBranch, pendingBranchSettings } from "@/lib/agents/branch-settings";
import { useActionIconAnimation } from "./use-action-icon-animation";

/**
 * Per-message hover action bar — React port of the legacy
 * `message-actions.js` / `-edit.js` / `-nav.js` trio.
 *
 * Sits in the message's `.message-actions-footer`, revealed on hover
 * (`.message:hover .message-actions`). Holds a timestamp badge and Copy,
 * then per role: a user message has Edit (a new version of the message,
 * files untouched), Rewind to here (lib/chat/rewind/rewind-actions.ts) and
 * Fork from here; a reply has
 * Regenerate and Pin as chapter (which pins the turn above it: the pin is
 * stored on that turn's user message, lib/chat/transcript/chapter-pin.ts);
 * last the `< N/M >` sibling-version navigator. Quoting is the
 * text selection's (editing/quote-to-chat.tsx); copying the conversation
 * is in the conversation's ⋯ menu (pane/view-controls.tsx).
 *
 * Regenerate / Edit / checkout hit the REST endpoints and then re-request
 * the conversation over the shared WS — the server moves HEAD and
 * `load_session` re-feeds the React store.
 */
import {
  cloneElement,
  isValidElement,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";

import { selectRow, selectViewRows, useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { copyText } from "@/lib/clipboard";
import { forkFromMessage, requestRewind } from "@/lib/chat/rewind/rewind-actions";
import { disabledReason, isRewindable } from "@/lib/chat/rewind/rewind-logic";
import { useForkDisabled, useRewindAllowed, useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";
import { setRunActive } from "@/lib/runtime-bridge/session/chat-handlers";
import { runtimeState } from "@/lib/runtime-bridge/state";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { formatMessageTime } from "@/lib/chat/transcript/message-time";
import { turnUserMessageId } from "@/lib/chat/transcript/chapter";
import { setTurnChapter } from "@/lib/chat/transcript/chapter-pin";
import { GitFork } from "lucide-react";
import {
  BookmarkIcon,
  CheckIcon,
  CopyIcon,
  RefreshCwIcon,
  SquarePenIcon,
  UndoIcon,
} from "@/components/animated-icons";
import { useRowSessionId } from "../list/transcript-view-scope";

/** Commands go out as they are; a `load_session` goes through the managed
 *  session read, since the transcript applies only the reply to a read it
 *  registered. */
function wsSend(payload: unknown): boolean {
  return sendRuntimeCommand(payload);
}

// Tools / action glyphs are the animated line icons (pqoqubbw, in
// ../../animated-icons). ActionButton drives the entire hover/focus target.
// chevL/chevR stay static (tiny ‹ › nav carets — not worth animating).
export const SVG = {
  copy: <CopyIcon />,
  check: <CheckIcon />,
  retry: <RefreshCwIcon />,
  pencil: <SquarePenIcon />,
  undo: <UndoIcon />,
  fork: <GitFork aria-hidden />,
  pin: <BookmarkIcon />,
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

/** Official ghost round icon button at the size closest to the 26px
 *  design (icon-xs, 24px); the glyph keeps its 16px size through
 *  message-actions.css. */
const ACTION_BUTTON_CLASS = buttonVariants({ variant: "ghost", size: "icon-xs" });

/** A message action whose whole button controls its shared animated icon.
 *  The label shows as a HoverTip (never a native title) and stays the
 *  button's aria-label. `detail` is the tip's second line. With
 *  `disabledReason` the button is disabled and the tip is that one line,
 *  on a wrapper because a disabled button gets no pointer events. */
export function ActionButton({
  icon,
  title,
  detail,
  disabledReason,
  onClick,
  disabled,
  extraClass,
  pressed,
}: {
  icon: ReactNode;
  title: string;
  detail?: string;
  disabledReason?: string;
  onClick: () => void;
  disabled?: boolean;
  extraClass?: string;
  /** A toggle's state, exposed as aria-pressed. */
  pressed?: boolean;
}) {
  const off = disabled || Boolean(disabledReason);
  const { ref, handlers } = useActionIconAnimation(off);
  const node = isValidElement(icon)
    ? cloneElement(icon as ReactElement, { ref } as Record<string, unknown>)
    : icon;
  const button = (
    <button
      type="button"
      className={cn(ACTION_BUTTON_CLASS, "message-action-btn", extraClass)}
      aria-label={title}
      aria-description={disabledReason ? undefined : detail}
      aria-pressed={pressed}
      disabled={off}
      onClick={onClick}
      {...handlers}
    >
      {node}
    </button>
  );
  return (
    <HoverTip label={disabledReason ? <TipBody title={disabledReason} /> : <TipBody title={title} detail={detail} />}>
      {disabledReason ? <span className="inline-flex" tabIndex={0}>{button}</span> : button}
    </HoverTip>
  );
}

/** The < > sibling-version arrow: the same ghost round button as
 *  ActionButton, with a HoverTip instead of a native title. */
export function NavButton({
  dir,
  label,
  disabled,
  onClick,
}: {
  dir: "prev" | "next";
  label: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <HoverTip label={<TipBody title={label} />}>
      <button
        type="button"
        className={cn(ACTION_BUTTON_CLASS, "message-nav-btn")}
        data-nav={dir}
        aria-label={label}
        disabled={disabled}
        onClick={onClick}
      >
        {dir === "prev" ? SVG.chevL : SVG.chevR}
      </button>
    </HoverTip>
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
      data-tip={fullTime}
      aria-label={fullTime}
      tabIndex={0}
    >
      {formatMessageTime(value)}
    </span>
  );
}

export function MessageActions({
  msg,
  onEdit,
  sessionIdOverride,
}: {
  msg: ChatMsg;
  onEdit?: () => void;
  sessionIdOverride?: string;
}) {
  const { text: tr } = useTranslation();
  const sessionId = useRowSessionId(sessionIdOverride);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  // One pin request at a time; the store already shows the new state, so
  // the button does not grey out while the server answers.
  const pinBusy = useRef(false);
  // A reply pins the turn above it: the user message that opened the turn.
  // Both reads are scalars, so streaming deltas elsewhere do not re-render.
  const turnUserId = useSessionStore((s) =>
    msg.role === "assistant" && sessionId
      ? turnUserMessageId(s.messageOrder?.[sessionId], selectViewRows(sessionId)(s), msg.id)
      : undefined,
  );
  const chapterPinned = useSessionStore((s) =>
    Boolean(selectRow(sessionId, turnUserId)(s)?.chapterPinnedAt),
  );
  const sent = Boolean(sessionId) && isRewindable(msg);
  const rewindAllowed = useRewindAllowed(sessionId);
  const rewindable = sent && rewindAllowed;
  const rewindCause = useRewindDisabled(rewindable ? sessionId : null);
  const forkCause = useForkDisabled(sent ? sessionId : null, msg.id);
  const rewindDisabled = rewindCause ? disabledReason(rewindCause, tr) : undefined;

  function copy() {
    const text = msg.content || "";
    if (!text) return;
    void copyText(text).then((ok) => {
      if (!ok) {
        showToast(tr("Could not copy the message", "未能复制消息"), { tone: "error" });
        return;
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    });
  }

  function retry() {
    if (!sessionId || !msg.id || busy) return;
    setBusy(true);
    postJson("/api/chat/retry", { session_id: sessionId, msg_id: msg.id })
      .then(() => {
        if (useSessionStore.getState().currentSessionId === sessionId) setRunActive(true);
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] retry failed:", err);
        setBusy(false);
      });
  }

  function toggleChapter() {
    if (!sessionId || !turnUserId || pinBusy.current) return;
    const pin = !chapterPinned;
    pinBusy.current = true;
    void setTurnChapter(sessionId, turnUserId, pin).then((ok) => {
      pinBusy.current = false;
      if (!ok) {
        showToast(
          pin
            ? tr("Could not pin the chapter", "未能钉为章节")
            : tr("Could not unpin the chapter", "未能取消章节"),
          { tone: "error" },
        );
      }
    });
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
    store.applyLocalChange(sessionId, {
      kind: "overlay_set", rowId: msg.id,
      fields: { siblingIndex: (msg.siblingIndex ?? 0) + dir }, clear: ["until_read"],
    });
    Promise.resolve(pendingBranchSettings(sessionId)).then(() => postJson("/api/chat/checkout", { session_id: sessionId, msg_id: targetId }))
      .then((reply) => {
        adoptCheckedOutBranch(sessionId, reply);
        runtimeState._postCheckoutScrollTo = targetId;
        wsSend({ action: "load_session", session_id: sessionId });
      })
      .catch((err) => {
        console.error("[message-actions] checkout failed:", err);
        useSessionStore.getState().applyLocalChange(sessionId, { kind: "overlay_clear", rowId: msg.id });
        showToast(tr("Version switch failed.", "版本切换失败。"), { tone: "error" });
        setBusy(false);
      });
  }

  const total = msg.siblingTotal ?? 0;
  const idx = msg.siblingIndex ?? 0;

  return (
    <div className={cn("message-actions", chapterPinned && "is-chapter-pinned")}>
      <MessageTimestamp timestamp={msg.timestamp} />
      <ActionButton
        icon={copied ? SVG.check : SVG.copy}
        title={tr("Copy", "复制")}
        extraClass={copied ? "is-copied" : undefined}
        onClick={copy}
      />
      {msg.role === "assistant" ? (
        <ActionButton
          icon={SVG.retry}
          title={tr("Regenerate", "重新生成")}
          disabled={busy}
          onClick={retry}
        />
      ) : null}
      {turnUserId ? (
        <ActionButton
          icon={SVG.pin}
          title={chapterPinned ? tr("Unpin chapter", "取消章节") : tr("Pin as chapter", "钉为章节")}
          pressed={chapterPinned}
          extraClass={chapterPinned ? "is-chapter" : undefined}
          onClick={toggleChapter}
        />
      ) : null}
      {onEdit ? (
        <ActionButton
          icon={SVG.pencil}
          title={tr("Edit message", "编辑消息")}
          onClick={onEdit}
        />
      ) : null}
      {rewindable ? (
        <ActionButton
          icon={SVG.undo}
          title={tr("Rewind to here", "回退到这里")}
          detail={tr(
            "Takes this message and everything after it out of the conversation.",
            "把这条消息及之后的内容撤出对话。",
          )}
          disabledReason={rewindDisabled}
          onClick={() => void requestRewind(sessionId!, msg.id, msg.content ?? "")}
        />
      ) : null}
      {sent && !forkCause ? (
        <ActionButton
          icon={SVG.fork}
          title={tr("Fork from here", "从这里分叉")}
          detail={tr("Starts a new conversation from this point. This one stays as it is.", "从这里开启新对话，这段对话保持不变。")}
          onClick={() => void forkFromMessage(sessionId!, msg.id)}
        />
      ) : null}
      {total > 1 ? (
        <div className="message-nav">
          <NavButton
            dir="prev"
            label={tr("Previous version", "上一个版本")}
            disabled={busy || idx <= 1}
            onClick={() => checkout(msg.prevSiblingId, -1)}
          />
          <span className="message-nav-label">
            {idx} / {total}
          </span>
          <NavButton
            dir="next"
            label={tr("Next version", "下一个版本")}
            disabled={busy || idx >= total}
            onClick={() => checkout(msg.nextSiblingId, 1)}
          />
        </div>
      ) : null}
    </div>
  );
}
