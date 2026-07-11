"use client";

/**
 * Function-call block — renders ANY function call (agentic
 * @agentic_function OR a regular LLM tool call surfaced via /run-style
 * runtime envelope) as a unified ``.inline-tree`` card, the same shape
 * used by ``ToolsBlock`` for regular tool calls. No outer
 * ``runtime-block`` frame, no separate "return:" preview — the
 * execution tree IS the visualisation.
 *
 * Header label = ``fnName(params)`` so the user immediately sees what
 * was called with what kwargs. Retry / attempt-nav move into the
 * ``inline-tree-actions`` slot so the card has one frame, not two.
 */
import { useEffect, useRef, useState } from "react";

import { formatUsageFooterLabel } from "@/lib/format-utils/format";
import { useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { optimisticAction } from "@/lib/runtime-bridge/optimistic-action";

import type { TNode } from "./execution-dag/types";
import { StepRow, TreeStep, decodeEscapes } from "./execution-strip";
import { ActionButton, SVG } from "./message-actions";
import { useMarkdownReady } from "./markdown";

interface RuntimeLegacyGlobals {
  renderMathInElement?: (el: HTMLElement, opts: unknown) => void;
}

function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(JSON.stringify(payload));
  return true;
}

/** Move HEAD to a sibling version, then reload so the transcript shows
 *  only that branch's run. Same op the chat-message ``< N/M >`` nav uses
 *  (POST /api/chat/checkout) — a pure display switch, nothing re-runs.
 *
 *  Optimistic (interaction-feedback policy): flip the CURRENT card into a
 *  spinner body + the target sibling index at 0ms so the click registers
 *  instantly. The checkout POST + ``load_session`` replaces the transcript
 *  (~1 round-trip) with the target branch's real run; that reload wipes this
 *  card's id from the store, which is our "settled" signal. On timeout we
 *  restore the pre-click card and toast. */
function checkoutSibling(
  sessionId: string,
  targetId: string,
  currentMsg: ChatMsg,
  targetIndex: number,
): void {
  const store = useSessionStore.getState();
  const id = currentMsg.id;
  const snapshot = store.messagesById[id];
  optimisticAction(
    {
      apply: () => {
        store.updateMessage(sessionId, id, {
          status: "running",
          contextTree: undefined,
          siblingIndex: targetIndex,
        });
      },
      // The load_session reload rebuilds the transcript with new ids, so
      // this card's id is gone from the store once the switch lands.
      settled: () => !useSessionStore.getState().messagesById[id],
      revert: () => {
        if (snapshot) useSessionStore.getState().updateMessage(sessionId, id, snapshot);
      },
      onTimeoutMessage: "Version switch timed out — reverted.",
    },
    showToast,
  );
  fetch("/api/chat/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, msg_id: targetId }),
  })
    .then(() => wsSend({ action: "load_session", session_id: sessionId }))
    .catch(() => {
      /* revert is handled by the optimisticAction timeout */
    });
}

/** Extract the function name from a ``run fn(args)`` / ``run fn arg1``
 *  command — RuntimeBlock no longer renders the params in its header,
 *  so we don't need to parse them out. */
function parseRun(cmd: string): { fn: string } {
  const text = cmd.replace(/^(run|create|fix)\s+/i, "").trim();
  const paren = text.match(/^([\w.-]+)\s*\(/);
  if (paren) return { fn: paren[1] };
  const sp = text.indexOf(" ");
  return { fn: sp < 0 ? text : text.slice(0, sp) };
}

/** Tree for this run. Each Retry is a separate sibling node with its
 *  OWN contextTree (only the active branch's node renders), so we read
 *  the node's tree directly — no per-message attempts array anymore. */
function displayTree(msg: ChatMsg): unknown {
  return msg.contextTree || null;
}

export function RuntimeBlock({
  msg,
  nested,
}: {
  msg: ChatMsg;
  /** True when rendered inside an assistant bubble (i.e. the call was
   *  initiated by the LLM itself, not by the user via fn-form). The
   *  user can't usefully "retry" a call the model made on its own —
   *  hide the Retry button in that mode, keep Copy JSON. */
  nested?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const { text } = useTranslation();
  const [copied, setCopied] = useState(false);
  useMarkdownReady();

  const sessionId = useSessionStore((s) => s.currentSessionId);
  const streaming =
    msg.status === "streaming" ||
    msg.status === "pending" ||
    msg.status === "running";
  const { fn } = parseRun(msg.function || msg.content || "");
  const fnName = msg.function || fn;
  const tree = displayTree(msg);

  useEffect(() => {
    const el = ref.current;
    const renderMath = (window as unknown as RuntimeLegacyGlobals)
      .renderMathInElement;
    if (el && renderMath) {
      try {
        renderMath(el, {
          delimiters: [
            { left: "$$", right: "$$", display: true },
            { left: "$", right: "$", display: false },
          ],
        });
      } catch {
        /* ignore */
      }
    }
  }, [tree]);

  // Version navigation: a Retry forks the call as a SIBLING branch, so
  // the runs are DAG siblings (same predecessor) — navigated with the
  // same < N/M > switcher chat messages use, via HEAD checkout. Only the
  // active sibling is on the current branch, so the transcript renders
  // exactly one run; the switcher (and the Branches panel) reach the rest.
  const siblingIdx = msg.siblingIndex ?? 0; // 1-based
  const siblingTotal = msg.siblingTotal ?? 0;
  const hasSiblings = siblingTotal > 1;
  const usageHtml = !streaming
    ? formatUsageFooterLabel(
        (msg.usage as Parameters<typeof formatUsageFooterLabel>[0]) || null,
      )
    : "";

  // Static header label — the function name + args are already
  // visible on the body's root tree row, so repeating them up here
  // both wastes space and forces a choice ("which call gets the
  // title?") that has no good answer when the tree contains many
  // nested calls. Drop the signature; keep the frame.
  const headerLabel = text("Function call", "函数调用");

  // Re-run the SAME function with its LAST kwargs in the SAME session.
  // The backend looks up the prior call's stored args and dispatches via
  // the forced-tool-call path (fresh sibling run, not an overwrite).
  function doRetry() {
    if (!sessionId) return;
    // 0ms feedback (interaction-feedback policy): flip THIS card into the
    // new-version pending state right now; the reload on
    // running_task_clear backfills the real run. Stuck retry reverts.
    const store = useSessionStore.getState();
    const rid = msg.id;
    const snapshot = store.messagesById[rid];
    const total = (msg.siblingTotal ?? 0) + 1;
    optimisticAction(
      {
        apply: () => {
          store.updateMessage(sessionId, rid, {
            status: "running",
            contextTree: undefined,
            siblingIndex: total,
            siblingTotal: total,
          });
        },
        settled: () => !useSessionStore.getState().messagesById[rid],
        revert: () => {
          if (snapshot) {
            useSessionStore.getState().updateMessage(sessionId, rid, snapshot);
          }
        },
        onTimeoutMessage: text(
          "Retry timed out — reverted.",
          "重试超时——已还原。",
        ),
      },
      showToast,
    );
    (window as Window & { __reloadOnTaskClear?: string | null }
    ).__reloadOnTaskClear = sessionId;
    wsSend({ action: "retry_function", session_id: sessionId, function: fnName });
  }

  // 复制 = 根调用的返回值（用户关心的是结果，不是内部树结构）；
  // 还没有输出时退回 "函数名 + 入参"。
  function copyResult() {
    const root = tree as TNode | null;
    const out = root?.error ?? root?.output;
    const payload = out !== undefined && out !== null && String(out).trim() !== ""
      ? decodeEscapes(String(out))
      : JSON.stringify({ function: fnName, params: root?.params }, null, 2);
    const done = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(payload).then(done, done);
    } else done();
  }

  // 修改：把当初的调用命令回填到输入框，用户改完直接发。
  function editCall() {
    const root = tree as TNode | null;
    const cmd = (msg.content || "").trim()
      || `run ${fnName}(${Object.entries(root?.params || {})
           .map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})`;
    useSessionStore.getState().setComposerInput(cmd);
  }

  const ts = msg.timestamp
    ? new Date(msg.timestamp > 1e12 ? msg.timestamp : msg.timestamp * 1000)
    : null;

  // 底部操作行：与聊天消息的 footer 同款（悬停显现的图标行），
  // 不再把重试/版本切换塞在根行右侧。仅顶层手动运行渲染；
  // 嵌在 assistant 气泡里的调用没有 footer。
  const footer = !nested ? (
    <div className="message-actions-footer runtime-actions-footer">
      <div className="message-actions">
        {ts ? (
          <span className="message-timestamp" title={ts.toLocaleString()}>
            {ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
        ) : null}
        <ActionButton
          icon={copied ? SVG.check : SVG.copy}
          title={text("Copy result", "复制结果")}
          extraClass={copied ? "is-copied" : undefined}
          onClick={copyResult}
        />
        {!streaming && fnName ? (
          <>
            <ActionButton
              icon={SVG.retry}
              title={text("Retry", "重试")}
              onClick={doRetry}
            />
            <ActionButton
              icon={SVG.pencil}
              title={text("Edit and re-run", "修改后重新运行")}
              onClick={editCall}
            />
          </>
        ) : null}
        {hasSiblings ? (
          <span className="attempt-nav" onClick={(e) => e.stopPropagation()}>
            <button
              className="attempt-nav-btn"
              disabled={siblingIdx <= 1 || !sessionId || !msg.prevSiblingId}
              title={text("Previous version", "上一个版本")}
              onClick={() =>
                sessionId &&
                msg.prevSiblingId &&
                checkoutSibling(sessionId, msg.prevSiblingId, msg, siblingIdx - 1)
              }
            >
              {"◀"}
            </button>
            <span className="attempt-nav-label">
              {siblingIdx}/{siblingTotal}
            </span>
            <button
              className="attempt-nav-btn"
              disabled={
                siblingIdx >= siblingTotal || !sessionId || !msg.nextSiblingId
              }
              title={text("Next version", "下一个版本")}
              onClick={() =>
                sessionId &&
                msg.nextSiblingId &&
                checkoutSibling(sessionId, msg.nextSiblingId, msg, siblingIdx + 1)
              }
            >
              {"▶"}
            </button>
          </span>
        ) : null}
      </div>
    </div>
  ) : null;

  // 与聊天时间线同一套组件（无框、圆图标压竖线）。手动运行的
  // 过程即内容：整棵树默认展开，根行挂重试/版本切换动作。
  // No tree yet (just-spawned placeholder) — a single running row.
  if (!tree) {
    return (
      <div
        ref={ref}
        className="tl"
        data-open="1"
        id={streaming ? "runtime_pending" : undefined}
        data-function={fnName || undefined}
        data-msg-id={msg.id}
      >
        <div className="tl-body">
          <StepRow
            icon="function"
            title={fnName || headerLabel}
            note={text("Running…", "运行中…")}
            running
          />
        </div>
        {footer}
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className="tl"
      data-open="1"
      id={streaming ? "runtime_pending" : undefined}
      data-function={fnName || undefined}
      data-msg-id={msg.id}
    >
      <div className="tl-body">
        <TreeStep node={tree as TNode} defaultKidsOpen />
      </div>
      {usageHtml ? (
        <div
          className="runtime-usage-footer"
          dangerouslySetInnerHTML={{ __html: usageHtml }}
        />
      ) : null}
      {footer}
    </div>
  );
}
